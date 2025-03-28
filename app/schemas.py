from pydantic import BaseModel, EmailStr, Field
from typing import List, Dict, Any, Optional
from datetime import datetime, date
from enum import Enum

# Auth schemas


class RequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    FLAGGED = "flagged"


class UserRole(str, Enum):
    ADMIN = "admin"
    ANALYST = "analyst"
    REVIEWER = "reviewer"
    USER = "user"


class UserModel(BaseModel):
    id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: EmailStr
    recovery_email: Optional[EmailStr] = None
    role: UserRole
    is_active: bool
    access_request_status: Optional[RequestStatus] = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = Field(default="bearer", pattern="^bearer$")
    expires_in: int = Field(gt=0)
    user: UserModel


class EmailVerificationRequest(BaseModel):
    email: EmailStr


class PasswordSetup(BaseModel):
    password: str = Field(..., min_length=8, example="string")
    confirm_password: str


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    email: Optional[EmailStr] = None
    exp: Optional[datetime] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# Quality issue schemas


class QualityIssueResponse(BaseModel):
    id: int
    portfolio_id: int
    issue_type: str
    description: str
    affected_records: List[Dict]
    severity: str
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class QualityIssueCreate(BaseModel):
    issue_type: str
    description: str
    affected_records: List[Dict]
    severity: str = "medium"


class QualityIssueComment(BaseModel):
    id: int
    quality_issue_id: int
    user_id: int
    comment: str
    created_at: datetime

    class Config:
        from_attributes = True


class QualityIssueCommentCreate(BaseModel):
    comment: str


class QualityIssueUpdate(BaseModel):
    status: Optional[str] = None
    description: Optional[str] = None
    severity: Optional[str] = None


class QualityCheckSummary(BaseModel):
    duplicate_names: int
    duplicate_addresses: int
    missing_repayment_data: int
    total_issues: int
    high_severity_issues: int
    open_issues: int


# Report schemas


class ReportTypeEnum(str, Enum):
    COLLATERAL_SUMMARY = "collateral_summary"
    GUARANTEE_SUMMARY = "guarantee_summary"
    INTEREST_RATE_SUMMARY = "interest_rate_summary"
    REPAYMENT_SUMMARY = "repayment_summary"
    ASSUMPTIONS_SUMMARY = "assumptions_summary"
    AMORTISED_LOAN_BALANCES = "amortised_loan_balances"
    PROBABILITY_DEFAULT = "probability_default"
    EXPOSURE_DEFAULT = "exposure_default"
    LOSS_GIVEN_DEFAULT = "loss_given_default"


class ReportBase(BaseModel):
    report_type: ReportTypeEnum
    report_date: date
    report_name: str


class ReportCreate(ReportBase):
    report_data: Dict[str, Any]
    portfolio_id: int


class ReportUpdate(BaseModel):
    report_name: Optional[str] = None
    report_data: Optional[Dict[str, Any]] = None


class ReportInDB(ReportBase):
    id: int
    portfolio_id: int
    created_at: datetime
    created_by: int
    report_data: Dict[str, Any]

    class Config:
        from_attributes = True


class ReportResponse(ReportInDB):
    pass


class ReportHistoryItem(BaseModel):
    id: int
    report_type: str
    report_date: date
    report_name: str
    created_at: datetime

    class Config:
        from_attributes = True


class ReportHistoryList(BaseModel):
    items: List[ReportHistoryItem]
    total: int


class ReportRequest(BaseModel):
    report_date: date
    report_type: ReportTypeEnum


class ReportSaveRequest(BaseModel):
    report_date: date
    report_type: ReportTypeEnum
    report_name: str
    report_data: Dict[str, Any]


# Portfolio schemas


class AssetType(str, Enum):
    EQUITY = "equity"
    DEBT = "debt"


class CustomerType(str, Enum):
    INDIVIDUALS = "individuals"
    INSTITUTION = "institution"
    MIXED = "mixed"


class FundingSource(str, Enum):
    PRIVATE_INVESTORS = "private investors"
    PENSION_FUND = "pension fund"
    MUTUAL_FUND = "mutual fund"
    OTHER_FUNDS = "other funds"


class DataSource(str, Enum):
    EXTERNAL_APPLICATION = "connect to external application"
    UPLOAD_DATA = "upload data"


class PortfolioCreate(BaseModel):
    name: str
    description: str
    asset_type: AssetType
    customer_type: CustomerType
    funding_source: FundingSource
    data_source: DataSource
    repayment_source: bool = False
    credit_risk_reserve: Optional[str] = None
    loan_assets: Optional[str] = None
    ecl_impairment_account: Optional[str] = None


# For updating an existing portfolio
class PortfolioUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    asset_type: Optional[AssetType] = None
    customer_type: Optional[CustomerType] = None
    funding_source: Optional[FundingSource] = None
    data_source: Optional[DataSource] = None
    repayment_source: Optional[bool] = None
    credit_risk_reserve: Optional[str] = None
    loan_assets: Optional[str] = None
    ecl_impairment_account: Optional[str] = None


class PortfolioResponse(BaseModel):
    id: int
    name: str
    description: str
    asset_type: str
    customer_type: str
    funding_source: str
    data_source: str
    repayment_source: bool
    credit_risk_reserve: Optional[str] = None
    loan_assets: Optional[str] = None
    ecl_impairment_account: Optional[str] = None
    user_id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PortfolioList(BaseModel):
    items: List[PortfolioResponse]
    total: int

    class Config:
        from_attributes = True


class OverviewModel(BaseModel):
    total_loans: int
    total_loan_value: float
    average_loan_amount: float
    total_customers: int

    class Config:
        from_attributes = True


class CustomerSummaryModel(BaseModel):
    individual_customers: int
    institutions: int
    mixed: int
    active_customers: int

    class Config:
        from_attributes = True


class PortfolioWithSummaryResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    asset_type: str
    customer_type: str
    funding_source: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    overview: Dict[str, Any]
    customer_summary: Dict[str, Any]
    quality_check: QualityCheckSummary
    quality_issues: Optional[List[QualityIssueResponse]] = None
    report_history: Optional[List[ReportHistoryItem]] = None

    class Config:
        from_attributes = True


# ECL schemas


class ECLCategoryData(BaseModel):
    """Data for each delinquency category row in the ECL grid"""

    num_loans: int
    total_loan_value: float
    provision_amount: float


# Impairment schemas
class ImpairmentCategory(BaseModel):
    """Configuration for an impairment category"""

    days_range: str  # Format: "0-30", "31-90", "360+" etc.
    rate: float


class ImpairmentCategoryData(BaseModel):
    """Data for each impairment category row"""

    days_range: str
    rate: float
    total_loan_value: float
    provision_amount: float


class ImpairmentSummaryMetrics(BaseModel):
    """Summary metrics for the impairment calculation"""

    total_loans: float
    total_provision: float


class LocalImpairmentSummary(BaseModel):
    """Response schema for the local impairment calculation endpoint"""

    portfolio_id: int
    calculation_date: str
    current: ImpairmentCategoryData
    olem: ImpairmentCategoryData
    substandard: ImpairmentCategoryData
    doubtful: ImpairmentCategoryData
    loss: ImpairmentCategoryData
    summary_metrics: ImpairmentSummaryMetrics


class ImpairmentConfig(BaseModel):
    """Configuration for all impairment categories"""

    current: ImpairmentCategory
    olem: ImpairmentCategory
    substandard: ImpairmentCategory
    doubtful: ImpairmentCategory
    loss: ImpairmentCategory


# Access request schemas
class AccessRequestSubmit(BaseModel):
    email: EmailStr
    admin_email: Optional[EmailStr] = None


class AccessRequestResponse(BaseModel):
    id: int
    email: EmailStr
    admin_email: Optional[EmailStr] = None
    status: str
    created_at: datetime
    is_email_verified: bool

    class Config:
        from_attributes = True


class AccessRequestUpdate(BaseModel):
    status: RequestStatus
    role: Optional[UserRole] = None


# User management schemas
class UserCreate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: EmailStr
    recovery_email: Optional[EmailStr] = None
    role: UserRole = UserRole.USER
    is_active: bool = True
    portfolio_id: Optional[int] = None


class UserResponse(BaseModel):
    id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: EmailStr
    recovery_email: Optional[EmailStr] = None
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class UserUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    recovery_email: Optional[EmailStr] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


# Feedback schemas


class FeedbackStatusEnum(str, Enum):
    SUBMITTED = "submitted"
    OPEN = "open"
    CLOSED = "closed"
    RETURNED = "returned"
    IN_DEVELOPMENT = "in development"
    COMPLETED = "completed"


class FeedbackBase(BaseModel):
    title: str
    description: str


class FeedbackCreate(FeedbackBase):
    pass


class FeedbackUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None


class FeedbackStatusUpdate(BaseModel):
    status: FeedbackStatusEnum


class FeedbackLikeResponse(BaseModel):
    id: int
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None

    class Config:
        from_attributes = True


class FeedbackResponse(FeedbackBase):
    id: int
    user_id: int
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    like_count: int
    is_liked_by_user: bool = False

    class Config:
        from_attributes = True


class FeedbackDetailResponse(FeedbackResponse):
    liked_by: List[FeedbackLikeResponse] = []

    class Config:
        from_attributes = True


# ECL schemas


class DaysRangeConfig(BaseModel):
    days_range: str = Field(..., example="0-30")


class ECLConfig(BaseModel):
    stage_1: DaysRangeConfig
    stage_2: DaysRangeConfig
    stage_3: DaysRangeConfig


class ECLStagingConfig(BaseModel):
    stage_1: DaysRangeConfig
    stage_2: DaysRangeConfig
    stage_3: DaysRangeConfig


class LocalImpairmentConfig(BaseModel):
    current: DaysRangeConfig
    olem: DaysRangeConfig
    substandard: DaysRangeConfig
    doubtful: DaysRangeConfig
    loss: DaysRangeConfig


class LGDInput(BaseModel):
    loan_amount: float
    outstanding_balance: float
    securities: List[dict] = []


class EADInput(BaseModel):
    loan_amount: float
    outstanding_balance: float
    loan_issue_date: date
    maturity_date: date
    reporting_date: date


class PDInput(BaseModel):
    ndia: int
    loan_type: Optional[str] = None


class EIRInput(BaseModel):
    loan_amount: float
    monthly_installment: float
    loan_term: int


class LoanStageInfo(BaseModel):
    loan_id: int
    employee_id: str
    stage: str
    outstanding_loan_balance: float
    ndia: int
    loan_issue_date: date
    loan_amount: float
    monthly_installment: float
    loan_term: int
    accumulated_arrears: float


class StagingResponse(BaseModel):
    loans: List[LoanStageInfo]


class CategoryData(BaseModel):
    num_loans: int
    total_loan_value: float
    provision_amount: float


class ECLSummaryMetrics(BaseModel):
    pd: float
    lgd: float
    ead: float
    total_provision: float
    provision_percentage: float


class ECLSummary(BaseModel):
    portfolio_id: int
    calculation_date: str
    stage_1: CategoryData
    stage_2: CategoryData
    stage_3: CategoryData
    summary_metrics: ECLSummaryMetrics


class LocalImpairmentCategoryData(BaseModel):
    num_loans: int
    total_loan_value: float
    provision_amount: float
    provision_rate: float


class LocalImpairmentSummary(BaseModel):
    portfolio_id: int
    calculation_date: str
    current: LocalImpairmentCategoryData
    olem: LocalImpairmentCategoryData
    substandard: LocalImpairmentCategoryData
    doubtful: LocalImpairmentCategoryData
    loss: LocalImpairmentCategoryData
    total_provision: float
    provision_percentage: float


class CalculatorResponse(BaseModel):
    result: float
    input_data: dict


class StagedLoans(BaseModel):
    portfolio_id: int
    loans: List[LoanStageInfo]


class ProvisionRateConfig(BaseModel):
    current: float = Field(..., example=0.01)
    olem: float = Field(..., example=0.03)
    substandard: float = Field(..., example=0.2)
    doubtful: float = Field(..., example=0.5)
    loss: float = Field(..., example=1.0)


class ECLComponentConfig(BaseModel):
    pd_factors: Dict[str, float] = Field(
        default_factory=lambda: {"stage_1": 0.01, "stage_2": 0.1, "stage_3": 0.5}
    )
    lgd_factors: Dict[str, float] = Field(
        default_factory=lambda: {"stage_1": 0.1, "stage_2": 0.3, "stage_3": 0.6}
    )
    ead_factors: Dict[str, float] = Field(
        default_factory=lambda: {"stage_1": 0.9, "stage_2": 0.95, "stage_3": 1.0}
    )


class CategoryData(BaseModel):
    num_loans: int
    total_loan_value: float
    provision_amount: float
    provision_rate: float


class LocalImpairmentSummary(BaseModel):
    portfolio_id: int
    calculation_date: str
    current: CategoryData
    olem: CategoryData
    substandard: CategoryData
    doubtful: CategoryData
    loss: CategoryData
    total_provision: float
    provision_percentage: float


class ECLSummaryMetrics(BaseModel):
    avg_pd: float
    avg_lgd: float
    avg_ead: float
    total_provision: float
    provision_percentage: float


class ECLSummary(BaseModel):
    portfolio_id: int
    calculation_date: str
    stage_1: CategoryData
    stage_2: CategoryData
    stage_3: CategoryData
    summary_metrics: ECLSummaryMetrics
