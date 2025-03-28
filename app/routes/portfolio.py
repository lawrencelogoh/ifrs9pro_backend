from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
    UploadFile,
    File,
    Form,
    Body,
)
from sqlalchemy.orm import Session, joinedload
import numpy as np
import math
from decimal import Decimal
from datetime import datetime, timedelta, date
from pydantic import BaseModel
from typing import List, Dict, Optional, Union
import pandas as pd
import io
from app.database import get_db
from app.models import Portfolio, User
from app.auth.utils import get_current_active_user
from app.calculators.ecl import (
    calculate_effective_interest_rate,
    calculate_exposure_at_default_percentage,
    calculate_probability_of_default,
    calculate_loss_given_default,
    calculate_marginal_ecl,
    is_in_range,
)
from app.calculators.local_impairment import (
    parse_days_range,
    calculate_category_data,
    calculate_days_past_due,
    calculate_loan_impairment,
    calculate_impairment_summary,
)
from app.models import (
    Portfolio,
    User,
    AssetType,
    CustomerType,
    FundingSource,
    DataSource,
    Loan,
    Security,
    Client,
    QualityIssue,
)
from app.schemas import (
    PortfolioCreate,
    PortfolioUpdate,
    PortfolioResponse,
    PortfolioList,
    PortfolioWithSummaryResponse,
    ECLSummary,
    ECLCategoryData,
    ECLSummaryMetrics,
    LocalImpairmentSummary,
    ImpairmentConfig,
    QualityIssueResponse,
    QualityIssueCreate,
    QualityIssueUpdate,
    QualityIssueComment,
    QualityIssueCommentCreate,
    QualityCheckSummary,
    ECLConfig,
    StagingResponse,
    ECLStagingConfig,
    LocalImpairmentConfig,
    CalculatorResponse,
    EADInput,
    PDInput,
    EIRInput,
    StagedLoans,
    ProvisionRateConfig,
    ECLComponentConfig,
    LoanStageInfo,
    CategoryData,
)
from app.auth.utils import get_current_active_user
from app.utils.quality_checks import create_quality_issues_if_needed
from app.utils.processors import (
    process_loan_details,
    process_loan_collateral,
    process_loan_guarantees,
)

router = APIRouter(prefix="/portfolios", tags=["portfolios"])


@router.post("/", response_model=PortfolioResponse, status_code=status.HTTP_201_CREATED)
def create_portfolio(
    portfolio: PortfolioCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Create a new portfolio for the current user.
    """
    new_portfolio = Portfolio(
        name=portfolio.name,
        description=portfolio.description,
        asset_type=portfolio.asset_type.value,
        customer_type=portfolio.customer_type.value,
        funding_source=portfolio.funding_source.value,
        data_source=portfolio.data_source.value,
        repayment_source=portfolio.repayment_source,
        credit_risk_reserve=portfolio.credit_risk_reserve,
        loan_assets=portfolio.loan_assets,
        ecl_impairment_account=portfolio.ecl_impairment_account,
        user_id=current_user.id,
    )

    db.add(new_portfolio)
    db.commit()
    db.refresh(new_portfolio)
    return new_portfolio


@router.get("/", response_model=PortfolioList)
def get_portfolios(
    skip: int = 0,
    limit: int = 100,
    asset_type: Optional[str] = None,
    customer_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Retrieve all portfolios belonging to the current user.
    Optional filtering by asset_type and customer_type.
    """
    query = db.query(Portfolio).filter(Portfolio.user_id == current_user.id)

    # Apply filters if provided
    if asset_type:
        query = query.filter(Portfolio.asset_type == asset_type)
    if customer_type:
        query = query.filter(Portfolio.customer_type == customer_type)

    # Get total count for pagination
    total = query.count()

    # Apply pagination
    portfolios = query.offset(skip).limit(limit).all()

    return {"items": portfolios, "total": total}


# Update the get_portfolio endpoint in your portfolios.py file


@router.get("/{portfolio_id}", response_model=PortfolioWithSummaryResponse)
def get_portfolio(
    portfolio_id: int,
    include_quality_issues: bool = False,
    include_report_history: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Retrieve a specific portfolio by ID including overview, customer summary, quality checks,
    and optionally report history.
    """
    # Query the portfolio with joined loans and clients
    portfolio = (
        db.query(Portfolio)
        .options(joinedload(Portfolio.loans), joinedload(Portfolio.clients))
        .filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
        .first()
    )

    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found"
        )

    # Calculate overview metrics
    total_loans = len(portfolio.loans)
    total_loan_value = sum(
        loan.loan_amount for loan in portfolio.loans if loan.loan_amount is not None
    )
    average_loan_amount = total_loan_value / total_loans if total_loans > 0 else 0
    total_customers = len(portfolio.clients)

    # Calculate customer summary metrics
    individual_customers = sum(
        1 for client in portfolio.clients if client.client_type == "consumer"
    )
    institutions = sum(
        1 for client in portfolio.clients if client.client_type == "institution"
    )
    mixed = sum(
        1
        for client in portfolio.clients
        if client.client_type not in ["consumer", "institution"]
    )
    # Determine active customers (you may need to adjust this logic based on your definition of "active")
    active_customers = sum(
        1
        for client in portfolio.clients
        if any(
            loan.paid is False
            for loan in portfolio.loans
            if hasattr(loan, "employee_id") and loan.employee_id == client.employee_id
        )
    )

    # Run quality checks and create issues if necessary
    quality_counts = create_quality_issues_if_needed(db, portfolio_id)

    quality_check_summary = QualityCheckSummary(
        duplicate_names=quality_counts["duplicate_names"],
        duplicate_addresses=quality_counts["duplicate_addresses"],
        missing_repayment_data=quality_counts["missing_repayment_data"],
        total_issues=quality_counts["total_issues"],
        high_severity_issues=quality_counts["high_severity_issues"],
        open_issues=quality_counts["open_issues"],
    )

    # Get quality issues if requested
    quality_issues = []
    if include_quality_issues:
        quality_issues = (
            db.query(QualityIssue)
            .filter(QualityIssue.portfolio_id == portfolio_id)
            .order_by(QualityIssue.severity.desc(), QualityIssue.created_at.desc())
            .all()
        )

    # Get report history if requested
    report_history = []
    if include_report_history:
        report_history = (
            db.query(Report)
            .filter(Report.portfolio_id == portfolio_id)
            .order_by(Report.created_at.desc())
            .all()
        )

    # Create response dictionary with portfolio data and summaries
    response = {
        "id": portfolio.id,
        "name": portfolio.name,
        "description": portfolio.description,
        "asset_type": portfolio.asset_type,
        "customer_type": portfolio.customer_type,
        "funding_source": portfolio.funding_source,
        "created_at": portfolio.created_at,
        "updated_at": portfolio.updated_at,
        "overview": {
            "total_loans": total_loans,
            "total_loan_value": round(total_loan_value, 2),
            "average_loan_amount": round(average_loan_amount, 2),
            "total_customers": total_customers,
        },
        "customer_summary": {
            "individual_customers": individual_customers,
            "institutions": institutions,
            "mixed": mixed,
            "active_customers": active_customers,
        },
        "quality_check": quality_check_summary,
        "quality_issues": quality_issues if include_quality_issues else None,
        "report_history": report_history if include_report_history else None,
    }

    return response


@router.put("/{portfolio_id}", response_model=PortfolioResponse)
def update_portfolio(
    portfolio_id: int,
    portfolio_update: PortfolioUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Update a specific portfolio by ID.
    """
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
        .first()
    )

    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found"
        )

    # Update fields if provided
    update_data = portfolio_update.dict(exclude_unset=True)

    # Convert enum values to strings for storage
    if "asset_type" in update_data and update_data["asset_type"]:
        update_data["asset_type"] = update_data["asset_type"].value
    if "customer_type" in update_data and update_data["customer_type"]:
        update_data["customer_type"] = update_data["customer_type"].value
    if "funding_source" in update_data and update_data["funding_source"]:
        update_data["funding_source"] = update_data["funding_source"].value
    if "data_source" in update_data and update_data["data_source"]:
        update_data["data_source"] = update_data["data_source"].value

    for key, value in update_data.items():
        setattr(portfolio, key, value)

    db.commit()
    db.refresh(portfolio)
    return portfolio


@router.delete("/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_portfolio(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Delete a specific portfolio by ID.
    """
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
        .first()
    )

    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found"
        )

    db.delete(portfolio)
    db.commit()

    return None


@router.post("/{portfolio_id}/ingest", status_code=status.HTTP_200_OK)
async def ingest_portfolio_data(
    portfolio_id: int,
    loan_details: Optional[UploadFile] = File(None),
    loan_guarantee_data: Optional[UploadFile] = File(None),
    loan_collateral_data: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Ingest Excel files containing portfolio data.

    Accepts up to three Excel files:
    - loan_details: Primary loan information
    - loan_guarantee_data: Information about loan guarantees
    - loan_collateral_data: Information about loan collateral
    """
    # Check if at least one file is provided
    if not any([loan_details, loan_guarantee_data, loan_collateral_data]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one file must be provided",
        )

    # Verify portfolio exists and belongs to current user
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
        .first()
    )

    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found"
        )

    results = {}

    # Start a transaction
    try:
        # Process loan details file
        if loan_details:
            results["loan_details"] = await process_loan_details(
                loan_details, portfolio_id, db
            )

        # Process loan guarantee data file
        if loan_guarantee_data:
            results["loan_guarantee_data"] = await process_loan_guarantees(
                loan_guarantee_data, portfolio_id, db
            )

        # Process loan collateral data file
        if loan_collateral_data:
            results["loan_collateral_data"] = await process_loan_collateral(
                loan_collateral_data, portfolio_id, db
            )

        # Commit all changes at once
        db.commit()

    except Exception as e:
        # Rollback in case of error
        db.rollback()
        return {
            "portfolio_id": portfolio_id,
            "results": {"error": str(e)},
            "status": "error",
        }

    return {"portfolio_id": portfolio_id, "results": results}


@router.post("/{portfolio_id}/stage-loans-ecl", response_model=StagingResponse)
def stage_loans_ecl(
    portfolio_id: int,
    config: ECLStagingConfig = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Classify loans in the portfolio according to ECL staging criteria (Stage 1, 2, 3).
    """
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
        .first()
    )
    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found"
        )
    # Parse day ranges from config
    try:
        stage_1_range = parse_days_range(config.stage_1.days_range)
        stage_2_range = parse_days_range(config.stage_2.days_range)
        stage_3_range = parse_days_range(config.stage_3.days_range)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Get all loans in the portfolio
    loans = db.query(Loan).filter(Loan.portfolio_id == portfolio_id).all()

    # Stage the loans
    staged_loans = []

    for loan in loans:

        # Determine the stage
        if is_in_range(loan.ndia, stage_1_range):
            stage = "Stage 1"
        elif is_in_range(loan.ndia, stage_2_range):
            stage = "Stage 2"
        elif is_in_range(loan.ndia, stage_3_range):
            stage = "Stage 3"
        else:
            # Default to Stage 3 if no stage matches
            stage = "Stage 3"

        outstanding_loan_balance = loan.outstanding_loan_balance
        ndia = int(loan.ndia)
        loan_issue_date = loan.loan_issue_date
        accumulated_arrears = loan.accumulated_arrears
        loan_amount = loan.loan_amount
        monthly_installment = loan.monthly_installment
        loan_term = loan.loan_term

        staged_loans.append(
            LoanStageInfo(
                loan_id=loan.id,
                employee_id=loan.employee_id,
                stage=stage,
                outstanding_loan_balance=outstanding_loan_balance,
                ndia=ndia,
                loan_issue_date=loan_issue_date,
                loan_amount=loan_amount,
                monthly_installment=monthly_installment,
                loan_term=loan_term,
                accumulated_arrears=accumulated_arrears,
            )
        )

    return StagingResponse(loans=staged_loans)


@router.post("/{portfolio_id}/stage-loans-local", response_model=StagingResponse)
def stage_loans_local_impairment(
    portfolio_id: int,
    config: LocalImpairmentConfig = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Classify loans in the portfolio according to local impairment categories:
    Current, OLEM, Substandard, Doubtful, and Loss.
    """
    # Verify portfolio exists and belongs to current user
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
        .first()
    )

    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found"
        )

    # Parse day ranges from config
    try:
        current_range = parse_days_range(config.current.days_range)
        olem_range = parse_days_range(config.olem.days_range)
        substandard_range = parse_days_range(config.substandard.days_range)
        doubtful_range = parse_days_range(config.doubtful.days_range)
        loss_range = parse_days_range(config.loss.days_range)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Get all loans in the portfolio
    loans = db.query(Loan).filter(Loan.portfolio_id == portfolio_id).all()

    # Stage the loans
    staged_loans = []

    for loan in loans:
        # Calculate NDIA if not available
        if loan.ndia is None:
            if (
                loan.accumulated_arrears
                and loan.monthly_installment
                and loan.monthly_installment > 0
            ):
                ndia = int(
                    (loan.accumulated_arrears / loan.monthly_installment) * 30
                )  # Convert months to days
            else:
                ndia = 0
        else:
            ndia = loan.ndia

        # Determine the category
        if is_in_range(ndia, current_range):
            stage = "Current"
        elif is_in_range(ndia, olem_range):
            stage = "OLEM"
        elif is_in_range(ndia, substandard_range):
            stage = "Substandard"
        elif is_in_range(ndia, doubtful_range):
            stage = "Doubtful"
        elif is_in_range(ndia, loss_range):
            stage = "Loss"

        outstanding_loan_balance = loan.outstanding_loan_balance
        ndia = int(loan.ndia)
        loan_issue_date = loan.loan_issue_date
        accumulated_arrears = loan.accumulated_arrears
        loan_amount = loan.loan_amount
        monthly_installment = loan.monthly_installment
        loan_term = loan.loan_term

        staged_loans.append(
            LoanStageInfo(
                loan_id=loan.id,
                employee_id=loan.employee_id,
                stage=stage,
                outstanding_loan_balance=outstanding_loan_balance,
                ndia=ndia,
                loan_issue_date=loan_issue_date,
                loan_amount=loan_amount,
                monthly_installment=monthly_installment,
                loan_term=loan_term,
                accumulated_arrears=accumulated_arrears,
            )
        )

    return StagingResponse(loans=staged_loans)


# Quality issue routes


@router.get("/{portfolio_id}/quality-issues", response_model=List[QualityIssueResponse])
def get_quality_issues(
    portfolio_id: int,
    status_type: Optional[str] = None,
    issue_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Retrieve quality issues for a specific portfolio.
    Optional filtering by status and issue type.
    """
    # Verify portfolio exists and belongs to current user
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
        .first()
    )

    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found"
        )

    # Build query for quality issues
    query = db.query(QualityIssue).filter(QualityIssue.portfolio_id == portfolio_id)
    
    # Apply filters if provided
    if status_type:
        query = query.filter(QualityIssue.status == status_type)
    if issue_type:
        query = query.filter(QualityIssue.issue_type == issue_type)

    # Order by severity (most severe first) and then by created date (newest first)
    quality_issues = query.order_by(
        QualityIssue.severity.desc(), QualityIssue.created_at.desc()
    ).all()

    if not quality_issues:
        raise HTTPException(
            status_code=status.HTTP_200_OK, detail="No quality issues found"
        )

    return quality_issues


@router.get("/{portfolio_id}/quality-issues/{issue_id}", response_model=QualityIssueResponse)
def get_quality_issue(
    portfolio_id: int,
    issue_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Retrieve a specific quality issue by ID.
    """
    # Verify portfolio exists and belongs to current user
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
        .first()
    )

    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found"
        )

    # Get the quality issue
    issue = (
        db.query(QualityIssue)
        .filter(QualityIssue.id == issue_id, QualityIssue.portfolio_id == portfolio_id)
        .first()
    )

    if not issue:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Quality issue not found"
        )

    return issue


@router.put("/{portfolio_id}/quality-issues/{issue_id}", response_model=QualityIssueResponse)
def update_quality_issue(
    portfolio_id: int,
    issue_id: int,
    issue_update: QualityIssueUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Update a quality issue, including approving it (changing status to "approved").
    """
    # Verify portfolio exists and belongs to current user
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
        .first()
    )

    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found"
        )

    # Get the quality issue
    issue = (
        db.query(QualityIssue)
        .filter(QualityIssue.id == issue_id, QualityIssue.portfolio_id == portfolio_id)
        .first()
    )

    if not issue:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Quality issue not found"
        )

    # Update fields if provided
    update_data = issue_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(issue, key, value)

    db.commit()
    db.refresh(issue)

    return issue


@router.post(
    "/{portfolio_id}/quality-issues/{issue_id}/comments",
    response_model=QualityIssueComment,
)
def add_comment_to_quality_issue(
    portfolio_id: int,
    issue_id: int,
    comment_data: QualityIssueCommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Add a comment to a quality issue.
    """
    # Verify portfolio exists and belongs to current user
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
        .first()
    )

    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found"
        )

    # Get the quality issue
    issue = (
        db.query(QualityIssue)
        .filter(QualityIssue.id == issue_id, QualityIssue.portfolio_id == portfolio_id)
        .first()
    )

    if not issue:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Quality issue not found"
        )

    # Create new comment
    new_comment = QualityIssueComment(
        quality_issue_id=issue_id, user_id=current_user.id, comment=comment_data.comment
    )

    db.add(new_comment)
    db.commit()
    db.refresh(new_comment)

    return new_comment


@router.get(
    "/{portfolio_id}/quality-issues/{issue_id}/comments",
    response_model=List[QualityIssueComment],
)
def get_quality_issue_comments(
    portfolio_id: int,
    issue_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Get all comments for a quality issue.
    """
    # Verify portfolio exists and belongs to current user
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
        .first()
    )

    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found"
        )

    # Get the quality issue
    issue = (
        db.query(QualityIssue)
        .filter(QualityIssue.id == issue_id, QualityIssue.portfolio_id == portfolio_id)
        .first()
    )

    if not issue:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Quality issue not found"
        )

    # Get all comments for this issue, ordered by creation date
    comments = (
        db.query(QualityIssueComment)
        .filter(QualityIssueComment.quality_issue_id == issue_id)
        .order_by(QualityIssueComment.created_at)
        .all()
    )

    return comments


@router.post(
    "/{portfolio_id}/quality-issues/{issue_id}/approve", response_model=QualityIssueResponse
)
def approve_quality_issue(
    portfolio_id: int,
    issue_id: int,
    comment: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Approve a quality issue, changing its status to "approved".
    Optionally add a comment about the approval.
    """
    # Verify portfolio exists and belongs to current user
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
        .first()
    )

    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found"
        )

    # Get the quality issue
    issue = (
        db.query(QualityIssue)
        .filter(QualityIssue.id == issue_id, QualityIssue.portfolio_id == portfolio_id)
        .first()
    )

    if not issue:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Quality issue not found"
        )

    # Update status to approved
    issue.status = "approved"

    # Add comment if provided
    if comment:
        new_comment = QualityIssueComment(
            quality_issue_id=issue_id,
            user_id=current_user.id,
            comment=f"Issue approved: {comment}",
        )
        db.add(new_comment)

    db.commit()
    db.refresh(issue)

    return issue


@router.post("/{portfolio_id}/approve-all-quality-issues", response_model=Dict)
def approve_all_quality_issues(
    portfolio_id: int,
    comment: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Approve all open quality issues for a portfolio at once.
    Optionally add the same comment to all issues.
    """
    # Verify portfolio exists and belongs to current user
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
        .first()
    )

    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found"
        )

    # Get all open quality issues
    open_issues = (
        db.query(QualityIssue)
        .filter(
            QualityIssue.portfolio_id == portfolio_id, QualityIssue.status == "open"
        )
        .all()
    )

    if not open_issues:
        return {"message": "No open quality issues to approve", "count": 0}

    # Update all issues to approved
    for issue in open_issues:
        issue.status = "approved"

        # Add comment if provided
        if comment:
            new_comment = QualityIssueComment(
                quality_issue_id=issue.id,
                user_id=current_user.id,
                comment=f"Batch approval: {comment}",
            )
            db.add(new_comment)

    db.commit()

    return {"message": "All quality issues approved", "count": len(open_issues)}


# This endpoint triggers a re-check of quality issues
@router.post("/{portfolio_id}/recheck-quality", response_model=QualityCheckSummary)
def recheck_quality_issues(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Run quality checks again to find any new issues.
    """
    # Verify portfolio exists and belongs to current user
    portfolio = (
        db.query(Portfolio)
        .filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id)
        .first()
    )

    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found"
        )

    # Run quality checks and create issues if necessary
    quality_counts = create_quality_issues_if_needed(db, portfolio_id)

    return QualityCheckSummary(
        duplicate_names=quality_counts["duplicate_names"],
        duplicate_addresses=quality_counts["duplicate_addresses"],
        missing_repayment_data=quality_counts["missing_repayment_data"],
        total_issues=quality_counts["total_issues"],
        high_severity_issues=quality_counts["high_severity_issues"],
        open_issues=quality_counts["open_issues"],
    )


@router.post("/calculate-local-provision", response_model=LocalImpairmentSummary)
def calculate_local_provision(
    staged_loans: StagedLoans,
    provision_config: ProvisionRateConfig = Body(...),
    reporting_date: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Calculate local impairment provisions based on pre-staged loans and provision rates.

    This route takes already-staged loans and applies the provision rates
    to calculate the required provision amounts.
    """
    # Use provided reporting date or default to current date
    if not reporting_date:
        reporting_date = datetime.now().date()

    # Verify portfolio belongs to current user
    portfolio = (
        db.query(Portfolio)
        .filter(
            Portfolio.id == staged_loans.portfolio_id,
            Portfolio.user_id == current_user.id,
        )
        .first()
    )
    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Portfolio not found or not authorized",
        )

    # Initialize category tracking
    current_loans = []
    olem_loans = []
    substandard_loans = []
    doubtful_loans = []
    loss_loans = []

    # Calculate totals for each category
    current_total = 0
    olem_total = 0
    substandard_total = 0
    doubtful_total = 0
    loss_total = 0

    # Process the pre-staged loans
    for loan_with_stage in staged_loans.loans:
        # Get the actual loan object from database for additional details if needed
        loan = db.query(Loan).filter(Loan.id == loan_with_stage.loan_id).first()
        if not loan:
            continue  # Skip if loan not found

        # Use the outstanding balance from the staged data
        outstanding_loan_balance = loan_with_stage.outstanding_loan_balance

        # Categorize loan by the pre-assigned stage
        stage = loan_with_stage.stage.lower()

        if stage == "current":
            current_loans.append(loan)
            current_total += outstanding_loan_balance
        elif stage == "olem":
            olem_loans.append(loan)
            olem_total += outstanding_loan_balance
        elif stage == "substandard":
            substandard_loans.append(loan)
            substandard_total += outstanding_loan_balance
        elif stage == "doubtful":
            doubtful_loans.append(loan)
            doubtful_total += outstanding_loan_balance
        elif stage == "loss":
            loss_loans.append(loan)
            loss_total += outstanding_loan_balance

    # Calculate provisions using the provision rates
    current_provision = current_total * provision_config.current
    olem_provision = olem_total * provision_config.olem
    substandard_provision = substandard_total * provision_config.substandard
    doubtful_provision = doubtful_total * provision_config.doubtful
    loss_provision = loss_total * provision_config.loss

    # Calculate total loan value and provision amount
    total_loan_value = (
        current_total + olem_total + substandard_total + doubtful_total + loss_total
    )
    total_provision = (
        current_provision
        + olem_provision
        + substandard_provision
        + doubtful_provision
        + loss_provision
    )

    # Calculate provision percentage
    provision_percentage = (
        (total_provision / total_loan_value * 100) if total_loan_value > 0 else 0
    )

    # Construct response
    response = LocalImpairmentSummary(
        portfolio_id=staged_loans.portfolio_id,
        calculation_date=reporting_date.strftime("%Y-%m-%d"),
        current=CategoryData(
            num_loans=len(current_loans),
            total_loan_value=round(current_total, 2),
            provision_amount=round(current_provision, 2),
            provision_rate=provision_config.current,
        ),
        olem=CategoryData(
            num_loans=len(olem_loans),
            total_loan_value=round(olem_total, 2),
            provision_amount=round(olem_provision, 2),
            provision_rate=provision_config.olem,
        ),
        substandard=CategoryData(
            num_loans=len(substandard_loans),
            total_loan_value=round(substandard_total, 2),
            provision_amount=round(substandard_provision, 2),
            provision_rate=provision_config.substandard,
        ),
        doubtful=CategoryData(
            num_loans=len(doubtful_loans),
            total_loan_value=round(doubtful_total, 2),
            provision_amount=round(doubtful_provision, 2),
            provision_rate=provision_config.doubtful,
        ),
        loss=CategoryData(
            num_loans=len(loss_loans),
            total_loan_value=round(loss_total, 2),
            provision_amount=round(loss_provision, 2),
            provision_rate=provision_config.loss,
        ),
        total_provision=round(total_provision, 2),
        provision_percentage=round(provision_percentage, 1),
    )

    return response


@router.post("/calculate-ecl-provision", response_model=ECLSummary)
def calculate_ecl_provision(
    staged_loans: StagedLoans,
    reporting_date: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Calculate ECL provisions based on pre-staged loans.

    This route takes already-staged loans and calculates ECL provisions using detailed
    calculation approach: Calculate PD, LGD, and EAD for each loan.
    """
    # Use provided reporting date or default to current date
    if not reporting_date:
        reporting_date = datetime.now().date()

    # Verify portfolio belongs to current user
    portfolio = (
        db.query(Portfolio)
        .filter(
            Portfolio.id == staged_loans.portfolio_id,
            Portfolio.user_id == current_user.id,
        )
        .first()
    )
    if not portfolio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Portfolio not found or not authorized",
        )

    # Initialize category tracking
    stage_1_loans = []
    stage_2_loans = []
    stage_3_loans = []

    # Calculate totals for each category
    stage_1_total = 0
    stage_2_total = 0
    stage_3_total = 0

    # Calculate provisions for each category
    stage_1_provision = 0
    stage_2_provision = 0
    stage_3_provision = 0

    # Summary metrics
    total_lgd = 0
    total_pd = 0
    total_ead_percentage = 0
    total_loans = 0

    # Get all client IDs to fetch securities
    client_ids = {loan.employee_id for loan in staged_loans.loans}

    # Get securities for all clients
    client_securities = {}
    if client_ids:
        securities = (
            db.query(Security)
            .join(Client, Security.client_id == Client.id)
            .filter(Client.employee_id.in_(client_ids))
            .all()
        )

        # Group securities by client employee_id
        for security in securities:
            client = db.query(Client).filter(Client.id == security.client_id).first()
            if client and client.employee_id:
                if client.employee_id not in client_securities:
                    client_securities[client.employee_id] = []
                client_securities[client.employee_id].append(security)

    # Process the pre-staged loans
    for loan_with_stage in staged_loans.loans:
        # Get the actual loan object from database for additional details
        loan = db.query(Loan).filter(Loan.id == loan_with_stage.loan_id).first()
        if not loan:
            continue  # Skip if loan not found

        # Use the outstanding balance from the staged data
        outstanding_loan_balance = loan_with_stage.outstanding_loan_balance

        # Get securities for this loan's client
        client_securities_list = client_securities.get(loan_with_stage.employee_id, [])

        # Categorize loan by the pre-assigned stage
        stage = loan_with_stage.stage.lower()
        ndia = loan_with_stage.ndia

        if stage == "stage 1":
            stage_1_loans.append(loan)
            stage_1_total += outstanding_loan_balance

            # Calculate components for each loan
            lgd = calculate_loss_given_default(loan, client_securities_list)
            pd = calculate_probability_of_default(loan, ndia)
            ead_percentage = calculate_exposure_at_default_percentage(
                loan, reporting_date
            )
            ecl = calculate_marginal_ecl(loan, ead_percentage, pd, lgd)

            # Update summary statistics
            total_lgd += lgd
            total_pd += pd
            total_ead_percentage += ead_percentage

            stage_1_provision += ecl

        elif stage == "stage 2":
            stage_2_loans.append(loan)
            stage_2_total += outstanding_loan_balance

            # Calculate components for each loan
            lgd = calculate_loss_given_default(loan, client_securities_list)
            pd = calculate_probability_of_default(loan, ndia)
            ead_percentage = calculate_exposure_at_default_percentage(
                loan, reporting_date
            )
            ecl = calculate_marginal_ecl(loan, ead_percentage, pd, lgd)

            # Update summary statistics
            total_lgd += lgd
            total_pd += pd
            total_ead_percentage += ead_percentage

            stage_2_provision += ecl

        elif stage == "stage 3":
            stage_3_loans.append(loan)
            stage_3_total += outstanding_loan_balance

            # Calculate components for each loan
            lgd = calculate_loss_given_default(loan, client_securities_list)
            pd = calculate_probability_of_default(loan, ndia)
            ead_percentage = calculate_exposure_at_default_percentage(
                loan, reporting_date
            )
            ecl = calculate_marginal_ecl(loan, ead_percentage, pd, lgd)

            # Update summary statistics
            total_lgd += lgd
            total_pd += pd
            total_ead_percentage += ead_percentage

            stage_3_provision += ecl

        # Increment total loan count
        total_loans += 1

    # Calculate averages for summary metrics
    avg_lgd = total_lgd / total_loans if total_loans > 0 else 0
    avg_pd = total_pd / total_loans if total_loans > 0 else 0
    avg_ead_percentage = total_ead_percentage / total_loans if total_loans > 0 else 0

    # Calculate total loan value and provision amount
    total_loan_value = stage_1_total + stage_2_total + stage_3_total
    total_provision = stage_1_provision + stage_2_provision + stage_3_provision

    # Calculate provision percentage
    provision_percentage = (
        (Decimal(total_provision) / Decimal(total_loan_value) * 100)
        if total_loan_value > 0
        else 0
    )

    # Calculate effective provision rates
    stage_1_rate = (
        Decimal(stage_1_provision) / Decimal(stage_1_total) if stage_1_total > 0 else 0
    )
    stage_2_rate = (
        Decimal(stage_2_provision) / Decimal(stage_2_total) if stage_2_total > 0 else 0
    )
    stage_3_rate = (
        Decimal(stage_3_provision) / Decimal(stage_3_total) if stage_3_total > 0 else 0
    )

    # Construct response
    response = ECLSummary(
        portfolio_id=staged_loans.portfolio_id,
        calculation_date=reporting_date.strftime("%Y-%m-%d"),
        stage_1=CategoryData(
            num_loans=len(stage_1_loans),
            total_loan_value=round(stage_1_total, 2),
            provision_amount=round(stage_1_provision, 2),
            provision_rate=round(stage_1_rate, 4),
        ),
        stage_2=CategoryData(
            num_loans=len(stage_2_loans),
            total_loan_value=round(stage_2_total, 2),
            provision_amount=round(stage_2_provision, 2),
            provision_rate=round(stage_2_rate, 4),
        ),
        stage_3=CategoryData(
            num_loans=len(stage_3_loans),
            total_loan_value=round(stage_3_total, 2),
            provision_amount=round(stage_3_provision, 2),
            provision_rate=round(stage_3_rate, 4),
        ),
        summary_metrics=ECLSummaryMetrics(
            avg_pd=round(avg_pd, 4),
            avg_lgd=round(avg_lgd, 4),
            avg_ead=round(avg_ead_percentage, 4),
            total_provision=round(total_provision, 2),
            provision_percentage=round(provision_percentage, 2),
        ),
    )

    return response
