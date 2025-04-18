"""
Utility functions for loan staging operations.
Contains implementations of ECL and local impairment staging.
"""
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from typing import Dict, Any, List, Tuple
from sqlalchemy import func
from decimal import Decimal

from app.models import StagingResult, Loan
from app.schemas import ECLStagingConfig, LocalImpairmentConfig

logger = logging.getLogger(__name__)

async def stage_loans_ecl_orm(portfolio_id: int, config: ECLStagingConfig, db: Session) -> Dict[str, Any]:
    """
    Implementation of ECL staging using SQLAlchemy ORM for large datasets.
    """
    try:
        logger.info(f"Starting ECL staging for portfolio {portfolio_id}")
        logger.info(f"ECL staging config: {config.dict()}")
        
        # Parse days ranges from config
        stage_1_range = config.stage_1.days_range
        stage_2_range = config.stage_2.days_range
        stage_3_range = config.stage_3.days_range
        
        logger.info(f"ECL staging ranges: Stage 1: {stage_1_range}, Stage 2: {stage_2_range}, Stage 3: {stage_3_range}")
        
        # Extract min and max days for each stage
        stage_1_min, stage_1_max = parse_days_range(stage_1_range)
        stage_2_min, stage_2_max = parse_days_range(stage_2_range)
        stage_3_min, stage_3_max = parse_days_range(stage_3_range)
        
        logger.info(f"Parsed day ranges: Stage 1: {stage_1_min}-{stage_1_max}, Stage 2: {stage_2_min}-{stage_2_max}, Stage 3: {stage_3_min}-{stage_3_max}")
        
        # Get total loan count for the portfolio
        total_loans = db.query(func.count(Loan.id)).filter(Loan.portfolio_id == portfolio_id).scalar() or 0
        logger.info(f"Total loans in portfolio {portfolio_id}: {total_loans}")
        
        # Initialize counters
        stage_counts = {1: 0, 2: 0, 3: 0}
        stage_balances = {1: 0.0, 2: 0.0, 3: 0.0}
        timestamp = datetime.now()
        
        # Use batch processing to reduce memory usage
        batch_size = 5000
        offset = 0
        
        # Sample logging for NDIA values
        ndia_sample = []
        sample_size = min(20, total_loans)
        
        while True:
            # Get a batch of loans
            loan_batch = db.query(Loan).filter(
                Loan.portfolio_id == portfolio_id
            ).order_by(Loan.id).offset(offset).limit(batch_size).all()
            
            # If no more loans, break the loop
            if not loan_batch:
                break
                
            # Process each loan in the batch
            for loan in loan_batch:
                # Get the ndia value (days past due)
                ndia = loan.ndia if loan.ndia is not None else 0
                
                # Sample some NDIA values for debugging
                if len(ndia_sample) < sample_size:
                    ndia_sample.append((loan.loan_no, ndia))
                
                # Get outstanding loan balance
                balance = float(loan.outstanding_loan_balance) if loan.outstanding_loan_balance is not None else 0.0
                
                # Determine the stage based on ndia
                if ndia >= stage_3_min:
                    loan.ecl_stage = 3
                    stage_counts[3] += 1
                    stage_balances[3] += balance
                elif ndia >= stage_2_min and (stage_2_max is None or ndia < stage_2_max):
                    loan.ecl_stage = 2
                    stage_counts[2] += 1
                    stage_balances[2] += balance
                else:
                    loan.ecl_stage = 1
                    stage_counts[1] += 1
                    stage_balances[1] += balance
                
                # Update the last staged timestamp
                loan.last_staged_at = timestamp
            
            # Commit changes for this batch
            db.commit()
            
            # Update offset for next batch
            offset += batch_size
            
            # Log progress
            logger.info(f"Processed {offset} loans out of {total_loans} for ECL staging")
        
        # Log sample NDIA values
        logger.info(f"Sample NDIA values from portfolio {portfolio_id}: {ndia_sample}")
        
        # Round balances to 2 decimal places
        stage_balances = {k: round(v, 2) for k, v in stage_balances.items()}
        
        # Log final stage counts and balances
        logger.info(f"ECL staging results for portfolio {portfolio_id}:")
        logger.info(f"Stage 1: {stage_counts[1]} loans, balance: {stage_balances[1]}")
        logger.info(f"Stage 2: {stage_counts[2]} loans, balance: {stage_balances[2]}")
        logger.info(f"Stage 3: {stage_counts[3]} loans, balance: {stage_balances[3]}")
        
        # Update the staging result
        staging_result = db.query(StagingResult).filter(
            StagingResult.portfolio_id == portfolio_id,
            StagingResult.staging_type == "ecl"
        ).order_by(StagingResult.created_at.desc()).first()
        
        if staging_result:
            staging_result.result_summary = {
                "status": "completed",
                "timestamp": timestamp.isoformat(),
                "total_loans": total_loans,
                "Stage 1": {
                    "num_loans": stage_counts.get(1, 0),
                    "outstanding_loan_balance": stage_balances.get(1, 0)
                },
                "Stage 2": {
                    "num_loans": stage_counts.get(2, 0),
                    "outstanding_loan_balance": stage_balances.get(2, 0)
                },
                "Stage 3": {
                    "num_loans": stage_counts.get(3, 0),
                    "outstanding_loan_balance": stage_balances.get(3, 0)
                },
                "config": {
                    "stage_1": {"days_range": stage_1_range},
                    "stage_2": {"days_range": stage_2_range},
                    "stage_3": {"days_range": stage_3_range}
                }
            }
            db.add(staging_result)
            db.commit()
        
        logger.info(f"Completed ECL staging for portfolio {portfolio_id}: {stage_counts}")
        
        # Return summary
        return {
            "status": "success",
            "total_loans": total_loans,
            "Stage 1": {
                "num_loans": stage_counts.get(1, 0),
                "outstanding_loan_balance": stage_balances.get(1, 0)
            },
            "Stage 2": {
                "num_loans": stage_counts.get(2, 0),
                "outstanding_loan_balance": stage_balances.get(2, 0)
            },
            "Stage 3": {
                "num_loans": stage_counts.get(3, 0),
                "outstanding_loan_balance": stage_balances.get(3, 0)
            }
        }
    
    except Exception as e:
        db.rollback()
        logger.error(f"Error in ECL staging: {str(e)}")
        return {
            "status": "error",
            "error": str(e)
        }

async def stage_loans_local_impairment_orm(portfolio_id: int, config: LocalImpairmentConfig, db: Session) -> Dict[str, Any]:
    """
    Implementation of local impairment staging using SQLAlchemy ORM for large datasets.
    """
    try:
        logger.info(f"Starting local impairment staging for portfolio {portfolio_id}")
        logger.info(f"Local impairment config: {config.dict()}")
        
        # Parse days ranges from config
        current_range = config.current.days_range
        olem_range = config.olem.days_range
        substandard_range = config.substandard.days_range
        doubtful_range = config.doubtful.days_range
        loss_range = config.loss.days_range
        
        logger.info(f"Local impairment ranges: Current: {current_range}, OLEM: {olem_range}, Substandard: {substandard_range}, Doubtful: {doubtful_range}, Loss: {loss_range}")
        
        # Extract min and max days for each category
        current_min, current_max = parse_days_range(current_range)
        olem_min, olem_max = parse_days_range(olem_range)
        substandard_min, substandard_max = parse_days_range(substandard_range)
        doubtful_min, doubtful_max = parse_days_range(doubtful_range)
        loss_min, loss_max = parse_days_range(loss_range)
        
        logger.info(f"Parsed day ranges: Current: {current_min}-{current_max}, OLEM: {olem_min}-{olem_max}, Substandard: {substandard_min}-{substandard_max}, Doubtful: {doubtful_min}-{doubtful_max}, Loss: {loss_min}-{loss_max}")
        
        # Get total loan count for the portfolio
        total_loans = db.query(func.count(Loan.id)).filter(Loan.portfolio_id == portfolio_id).scalar() or 0
        logger.info(f"Total loans in portfolio {portfolio_id} for local impairment: {total_loans}")
        
        # Initialize counters
        category_counts = {
            "Current": 0, 
            "OLEM": 0, 
            "Substandard": 0, 
            "Doubtful": 0, 
            "Loss": 0
        }
        category_balances = {
            "Current": 0.0, 
            "OLEM": 0.0, 
            "Substandard": 0.0, 
            "Doubtful": 0.0, 
            "Loss": 0.0
        }
        timestamp = datetime.now()
        
        # Use batch processing to reduce memory usage
        batch_size = 5000
        offset = 0
        
        # Sample logging for NDIA values
        ndia_sample = []
        sample_size = min(20, total_loans)
        
        while True:
            # Get a batch of loans
            loan_batch = db.query(Loan).filter(
                Loan.portfolio_id == portfolio_id
            ).order_by(Loan.id).offset(offset).limit(batch_size).all()
            
            # If no more loans, break the loop
            if not loan_batch:
                break
                
            # Process each loan in the batch
            for loan in loan_batch:
                # Get the ndia value (days past due)
                ndia = loan.ndia if loan.ndia is not None else 0
                
                # Sample some NDIA values for debugging
                if len(ndia_sample) < sample_size:
                    ndia_sample.append((loan.loan_no, ndia))
                
                # Get outstanding loan balance
                balance = float(loan.outstanding_loan_balance) if loan.outstanding_loan_balance is not None else 0.0
                
                # Determine the impairment category based on ndia
                if ndia >= 0 and (current_max is None or ndia < current_max):
                    loan.impairment_category = "Current"
                    category_counts["Current"] += 1
                    category_balances["Current"] += balance
                elif ndia >= olem_min and (olem_max is None or ndia < olem_max):
                    loan.impairment_category = "OLEM"
                    category_counts["OLEM"] += 1
                    category_balances["OLEM"] += balance
                elif ndia >= substandard_min and (substandard_max is None or ndia < substandard_max):
                    loan.impairment_category = "Substandard"
                    category_counts["Substandard"] += 1
                    category_balances["Substandard"] += balance
                elif ndia >= doubtful_min and (doubtful_max is None or ndia < doubtful_max):
                    loan.impairment_category = "Doubtful"
                    category_counts["Doubtful"] += 1
                    category_balances["Doubtful"] += balance
                elif ndia >= loss_min and (loss_max is None or ndia is not None):
                    loan.impairment_category = "Loss"
                    category_counts["Loss"] += 1
                    category_balances["Loss"] += balance
                
                # Update the last staged timestamp
                loan.last_staged_at = timestamp
            
            # Commit changes for this batch
            db.commit()
            
            # Update offset for next batch
            offset += batch_size
            
            # Log progress
            logger.info(f"Processed {offset} loans out of {total_loans} for local impairment staging")
        
        # Log sample NDIA values
        logger.info(f"Sample NDIA values from portfolio {portfolio_id} for local impairment: {ndia_sample}")
        
        # Round balances to 2 decimal places
        category_balances = {k: round(v, 2) for k, v in category_balances.items()}
        
        # Calculate provision amounts based on the provision rates
        current_rate = Decimal("0.01")  # 1% for Current
        olem_rate = Decimal("0.05")     # 5% for OLEM
        substandard_rate = Decimal("0.25")  # 25% for Substandard
        doubtful_rate = Decimal("0.5")  # 50% for Doubtful
        loss_rate = Decimal("1.0")     # 100% for Loss
        
        # Convert category balances to Decimal before multiplication
        current_provision = Decimal(str(category_balances["Current"])) * current_rate
        olem_provision = Decimal(str(category_balances["OLEM"])) * olem_rate
        substandard_provision = Decimal(str(category_balances["Substandard"])) * substandard_rate
        doubtful_provision = Decimal(str(category_balances["Doubtful"])) * doubtful_rate
        loss_provision = Decimal(str(category_balances["Loss"])) * loss_rate
        
        # Log final category counts and balances
        logger.info(f"Local impairment staging results for portfolio {portfolio_id}:")
        logger.info(f"Current: {category_counts['Current']} loans, balance: {category_balances['Current']}")
        logger.info(f"OLEM: {category_counts['OLEM']} loans, balance: {category_balances['OLEM']}")
        logger.info(f"Substandard: {category_counts['Substandard']} loans, balance: {category_balances['Substandard']}")
        logger.info(f"Doubtful: {category_counts['Doubtful']} loans, balance: {category_balances['Doubtful']}")
        logger.info(f"Loss: {category_counts['Loss']} loans, balance: {category_balances['Loss']}")
        
        # Update the staging result
        staging_result = db.query(StagingResult).filter(
            StagingResult.portfolio_id == portfolio_id,
            StagingResult.staging_type == "local_impairment"
        ).order_by(StagingResult.created_at.desc()).first()
        
        if staging_result:
            staging_result.result_summary = {
                "status": "completed",
                "timestamp": timestamp.isoformat(),
                "total_loans": total_loans,
                "Current": {
                    "num_loans": category_counts["Current"],
                    "outstanding_loan_balance": category_balances["Current"],
                    "total_loan_value": category_balances["Current"],
                    "provision_amount": float(current_provision),
                    "provision_rate": float(current_rate)
                },
                "OLEM": {
                    "num_loans": category_counts["OLEM"],
                    "outstanding_loan_balance": category_balances["OLEM"],
                    "total_loan_value": category_balances["OLEM"],
                    "provision_amount": float(olem_provision),
                    "provision_rate": float(olem_rate)
                },
                "Substandard": {
                    "num_loans": category_counts["Substandard"],
                    "outstanding_loan_balance": category_balances["Substandard"],
                    "total_loan_value": category_balances["Substandard"],
                    "provision_amount": float(substandard_provision),
                    "provision_rate": float(substandard_rate)
                },
                "Doubtful": {
                    "num_loans": category_counts["Doubtful"],
                    "outstanding_loan_balance": category_balances["Doubtful"],
                    "total_loan_value": category_balances["Doubtful"],
                    "provision_amount": float(doubtful_provision),
                    "provision_rate": float(doubtful_rate)
                },
                "Loss": {
                    "num_loans": category_counts["Loss"],
                    "outstanding_loan_balance": category_balances["Loss"],
                    "total_loan_value": category_balances["Loss"],
                    "provision_amount": float(loss_provision),
                    "provision_rate": float(loss_rate)
                },
                "config": {
                    "current": {"days_range": current_range},
                    "olem": {"days_range": olem_range},
                    "substandard": {"days_range": substandard_range},
                    "doubtful": {"days_range": doubtful_range},
                    "loss": {"days_range": loss_range}
                }
            }
            db.add(staging_result)
            db.commit()
        
        logger.info(f"Completed local impairment staging for portfolio {portfolio_id}: {category_counts}")
        
        # Return summary
        return {
            "status": "success",
            "total_loans": total_loans,
            "Current": {
                "num_loans": category_counts["Current"],
                "outstanding_loan_balance": category_balances["Current"],
                "provision_amount": float(current_provision)
            },
            "OLEM": {
                "num_loans": category_counts["OLEM"],
                "outstanding_loan_balance": category_balances["OLEM"],
                "provision_amount": float(olem_provision)
            },
            "Substandard": {
                "num_loans": category_counts["Substandard"],
                "outstanding_loan_balance": category_balances["Substandard"],
                "provision_amount": float(substandard_provision)
            },
            "Doubtful": {
                "num_loans": category_counts["Doubtful"],
                "outstanding_loan_balance": category_balances["Doubtful"],
                "provision_amount": float(doubtful_provision)
            },
            "Loss": {
                "num_loans": category_counts["Loss"],
                "outstanding_loan_balance": category_balances["Loss"],
                "provision_amount": float(loss_provision)
            }
        }
    
    except Exception as e:
        db.rollback()
        logger.error(f"Error in local impairment staging: {str(e)}")
        return {
            "status": "error",
            "error": str(e)
        }

def parse_days_range(days_range: str) -> Tuple[int, int]:
    """
    Parse a days range string like "0-30" or "90+" into min and max values.
    Returns a tuple of (min_days, max_days) where max_days is None for unbounded ranges.
    """
    if not days_range:
        return (0, None)
    
    if days_range.endswith("+"):
        min_days = int(days_range[:-1])
        max_days = None
    else:
        parts = days_range.split("-")
        if len(parts) != 2:
            raise ValueError(f"Invalid days range format: {days_range}")
        
        min_days = int(parts[0])
        max_days = int(parts[1])
    
    return (min_days, max_days)

def stage_loans_ecl_orm_sync(portfolio_id: int, config: ECLStagingConfig, db: Session) -> Dict[str, Any]:
    """
    Synchronous implementation of ECL staging using SQLAlchemy ORM for large datasets.
    """
    try:
        logger.info(f"Starting ECL staging for portfolio {portfolio_id}")
        logger.info(f"ECL staging config: {config.dict()}")
        
        # Parse days ranges from config
        stage_1_range = config.stage_1.days_range
        stage_2_range = config.stage_2.days_range
        stage_3_range = config.stage_3.days_range
        
        logger.info(f"ECL staging ranges: Stage 1: {stage_1_range}, Stage 2: {stage_2_range}, Stage 3: {stage_3_range}")
        
        # Extract min and max days for each stage
        stage_1_min, stage_1_max = parse_days_range(stage_1_range)
        stage_2_min, stage_2_max = parse_days_range(stage_2_range)
        stage_3_min, stage_3_max = parse_days_range(stage_3_range)
        
        logger.info(f"Parsed day ranges: Stage 1: {stage_1_min}-{stage_1_max}, Stage 2: {stage_2_min}-{stage_2_max}, Stage 3: {stage_3_min}-{stage_3_max}")
        
        # Get total loan count for the portfolio
        total_loans = db.query(func.count(Loan.id)).filter(Loan.portfolio_id == portfolio_id).scalar() or 0
        logger.info(f"Total loans in portfolio {portfolio_id}: {total_loans}")
        
        # Initialize counters
        stage_counts = {1: 0, 2: 0, 3: 0}
        stage_balances = {1: 0.0, 2: 0.0, 3: 0.0}
        timestamp = datetime.now()
        
        # Use batch processing to reduce memory usage
        batch_size = 5000
        offset = 0
        
        # Sample logging for NDIA values
        ndia_samples = []
        
        # Process loans in batches
        while True:
            # Get a batch of loans
            loans_batch = db.query(Loan).filter(Loan.portfolio_id == portfolio_id).limit(batch_size).offset(offset).all()
            
            if not loans_batch:
                break
                
            # Process each loan in the batch
            for loan in loans_batch:
                # Skip loans without days in arrears
                if loan.ndia is None:
                    continue
                    
                # Convert days in arrears to integer
                ndia = int(loan.ndia)
                
                # Sample some NDIA values for logging
                if len(ndia_samples) < 10:
                    ndia_samples.append(ndia)
                
                # Determine stage based on days in arrears
                if ndia >= 0 and (stage_1_max is None or ndia < stage_1_max):
                    loan.stage = "Stage 1"
                    stage_counts[1] += 1
                    if loan.outstanding_loan_balance:
                        stage_balances[1] += float(loan.outstanding_loan_balance)
                elif ndia >= stage_2_min and (stage_2_max is None or ndia < stage_2_max):
                    loan.stage = "Stage 2"
                    stage_counts[2] += 1
                    if loan.outstanding_loan_balance:
                        stage_balances[2] += float(loan.outstanding_loan_balance)
                elif ndia >= stage_3_min and (stage_3_max is None or ndia is not None):
                    loan.stage = "Stage 3"
                    stage_counts[3] += 1
                    if loan.outstanding_loan_balance:
                        stage_balances[3] += float(loan.outstanding_loan_balance)
            
            # Commit changes for this batch
            db.commit()
            
            # Move to next batch
            offset += batch_size
        
        # Log NDIA samples
        logger.info(f"Sample NDIA values: {ndia_samples}")
        
        # Log stage counts
        logger.info(f"Stage 1: {stage_counts[1]} loans, balance: {stage_balances[1]}")
        logger.info(f"Stage 2: {stage_counts[2]} loans, balance: {stage_balances[2]}")
        logger.info(f"Stage 3: {stage_counts[3]} loans, balance: {stage_balances[3]}")
        
        # Create a new staging result
        staging_result = StagingResult(
            portfolio_id=portfolio_id,
            staging_type="ecl",
            config={
                "stage_1": {"days_range": stage_1_range},
                "stage_2": {"days_range": stage_2_range},
                "stage_3": {"days_range": stage_3_range}
            },
            result_summary={
                "Stage 1": {
                    "num_loans": stage_counts[1],
                    "outstanding_loan_balance": stage_balances[1]
                },
                "Stage 2": {
                    "num_loans": stage_counts[2],
                    "outstanding_loan_balance": stage_balances[2]
                },
                "Stage 3": {
                    "num_loans": stage_counts[3],
                    "outstanding_loan_balance": stage_balances[3]
                }
            }
        )
        
        # Save the staging result
        db.add(staging_result)
        db.commit()
        
        return {
            "status": "success",
            "stage_1_count": stage_counts[1],
            "stage_2_count": stage_counts[2],
            "stage_3_count": stage_counts[3],
            "stage_1_balance": stage_balances[1],
            "stage_2_balance": stage_balances[2],
            "stage_3_balance": stage_balances[3],
            "total_loans": sum(stage_counts.values())
        }
        
    except Exception as e:
        logger.error(f"Error in ECL staging: {str(e)}")
        db.rollback()
        raise

def stage_loans_local_impairment_orm_sync(portfolio_id: int, config: LocalImpairmentConfig, db: Session) -> Dict[str, Any]:
    """
    Synchronous implementation of local impairment staging using SQLAlchemy ORM for large datasets.
    """
    try:
        logger.info(f"Starting local impairment staging for portfolio {portfolio_id}")
        logger.info(f"Local impairment staging config: {config.dict()}")
        
        # Parse days ranges from config
        current_range = config.current.days_range
        olem_range = config.olem.days_range
        substandard_range = config.substandard.days_range
        doubtful_range = config.doubtful.days_range
        loss_range = config.loss.days_range
        
        logger.info(f"Local impairment ranges: Current: {current_range}, OLEM: {olem_range}, Substandard: {substandard_range}, Doubtful: {doubtful_range}, Loss: {loss_range}")
        
        # Extract min and max days for each category
        current_min, current_max = parse_days_range(current_range)
        olem_min, olem_max = parse_days_range(olem_range)
        substandard_min, substandard_max = parse_days_range(substandard_range)
        doubtful_min, doubtful_max = parse_days_range(doubtful_range)
        loss_min, loss_max = parse_days_range(loss_range)
        
        logger.info(f"Parsed day ranges: Current: {current_min}-{current_max}, OLEM: {olem_min}-{olem_max}, Substandard: {substandard_min}-{substandard_max}, Doubtful: {doubtful_min}-{doubtful_max}, Loss: {loss_min}-{loss_max}")
        
        # Get total loan count for the portfolio
        total_loans = db.query(func.count(Loan.id)).filter(Loan.portfolio_id == portfolio_id).scalar() or 0
        logger.info(f"Total loans in portfolio {portfolio_id}: {total_loans}")
        
        # Initialize counters
        category_counts = {
            "Current": 0, 
            "OLEM": 0, 
            "Substandard": 0, 
            "Doubtful": 0, 
            "Loss": 0
        }
        category_balances = {
            "Current": 0.0, 
            "OLEM": 0.0, 
            "Substandard": 0.0, 
            "Doubtful": 0.0, 
            "Loss": 0.0
        }
        timestamp = datetime.now()
        
        # Use batch processing to reduce memory usage
        batch_size = 5000
        offset = 0
        
        # Process loans in batches
        while True:
            # Get a batch of loans
            loans_batch = db.query(Loan).filter(Loan.portfolio_id == portfolio_id).limit(batch_size).offset(offset).all()
            
            if not loans_batch:
                break
                
            # Process each loan in the batch
            for loan in loans_batch:
                # Skip loans without days in arrears
                if loan.ndia is None:
                    continue
                    
                # Convert days in arrears to integer
                ndia = int(loan.ndia)
                
                # Get outstanding balance
                balance = float(loan.outstanding_loan_balance if loan.outstanding_loan_balance is not None else 0)
                
                # Determine category based on days in arrears
                if ndia >= 0 and (current_max is None or ndia < current_max):
                    loan.impairment_category = "Current"
                    category_counts["Current"] += 1
                    category_balances["Current"] += balance
                elif ndia >= olem_min and (olem_max is None or ndia < olem_max):
                    loan.impairment_category = "OLEM"
                    category_counts["OLEM"] += 1
                    category_balances["OLEM"] += balance
                elif ndia >= substandard_min and (substandard_max is None or ndia < substandard_max):
                    loan.impairment_category = "Substandard"
                    category_counts["Substandard"] += 1
                    category_balances["Substandard"] += balance
                elif ndia >= doubtful_min and (doubtful_max is None or ndia < doubtful_max):
                    loan.impairment_category = "Doubtful"
                    category_counts["Doubtful"] += 1
                    category_balances["Doubtful"] += balance
                elif ndia >= loss_min and (loss_max is None or ndia is not None):
                    loan.impairment_category = "Loss"
                    category_counts["Loss"] += 1
                    category_balances["Loss"] += balance
            
            # Commit changes for this batch
            db.commit()
            
            # Move to next batch
            offset += batch_size
        
        # Log category counts
        logger.info(f"Current: {category_counts['Current']} loans, balance: {category_balances['Current']}")
        logger.info(f"OLEM: {category_counts['OLEM']} loans, balance: {category_balances['OLEM']}")
        logger.info(f"Substandard: {category_counts['Substandard']} loans, balance: {category_balances['Substandard']}")
        logger.info(f"Doubtful: {category_counts['Doubtful']} loans, balance: {category_balances['Doubtful']}")
        logger.info(f"Loss: {category_counts['Loss']} loans, balance: {category_balances['Loss']}")
        
        # Calculate provision amounts based on the provision rates
        current_rate = Decimal("0.01")  # 1% for Current
        olem_rate = Decimal("0.05")     # 5% for OLEM
        substandard_rate = Decimal("0.25")  # 25% for Substandard
        doubtful_rate = Decimal("0.5")  # 50% for Doubtful
        loss_rate = Decimal("1.0")     # 100% for Loss
        
        # Convert category balances to Decimal before multiplication
        current_provision = Decimal(str(category_balances["Current"])) * current_rate
        olem_provision = Decimal(str(category_balances["OLEM"])) * olem_rate
        substandard_provision = Decimal(str(category_balances["Substandard"])) * substandard_rate
        doubtful_provision = Decimal(str(category_balances["Doubtful"])) * doubtful_rate
        loss_provision = Decimal(str(category_balances["Loss"])) * loss_rate
        
        # Create a new staging result
        staging_result = StagingResult(
            portfolio_id=portfolio_id,
            staging_type="local_impairment",
            config={
                "current": {"days_range": current_range},
                "olem": {"days_range": olem_range},
                "substandard": {"days_range": substandard_range},
                "doubtful": {"days_range": doubtful_range},
                "loss": {"days_range": loss_range}
            },
            result_summary={
                "Current": {
                    "num_loans": category_counts["Current"],
                    "outstanding_loan_balance": category_balances["Current"],
                    "total_loan_value": category_balances["Current"],
                    "provision_amount": float(current_provision),
                    "provision_rate": float(current_rate)
                },
                "OLEM": {
                    "num_loans": category_counts["OLEM"],
                    "outstanding_loan_balance": category_balances["OLEM"],
                    "total_loan_value": category_balances["OLEM"],
                    "provision_amount": float(olem_provision),
                    "provision_rate": float(olem_rate)
                },
                "Substandard": {
                    "num_loans": category_counts["Substandard"],
                    "outstanding_loan_balance": category_balances["Substandard"],
                    "total_loan_value": category_balances["Substandard"],
                    "provision_amount": float(substandard_provision),
                    "provision_rate": float(substandard_rate)
                },
                "Doubtful": {
                    "num_loans": category_counts["Doubtful"],
                    "outstanding_loan_balance": category_balances["Doubtful"],
                    "total_loan_value": category_balances["Doubtful"],
                    "provision_amount": float(doubtful_provision),
                    "provision_rate": float(doubtful_rate)
                },
                "Loss": {
                    "num_loans": category_counts["Loss"],
                    "outstanding_loan_balance": category_balances["Loss"],
                    "total_loan_value": category_balances["Loss"],
                    "provision_amount": float(loss_provision),
                    "provision_rate": float(loss_rate)
                }
            }
        )
        
        # Save the staging result
        db.add(staging_result)
        db.commit()
        
        return {
            "status": "success",
            "current_count": category_counts["Current"],
            "olem_count": category_counts["OLEM"],
            "substandard_count": category_counts["Substandard"],
            "doubtful_count": category_counts["Doubtful"],
            "loss_count": category_counts["Loss"],
            "current_balance": category_balances["Current"],
            "olem_balance": category_balances["OLEM"],
            "substandard_balance": category_balances["Substandard"],
            "doubtful_balance": category_balances["Doubtful"],
            "loss_balance": category_balances["Loss"],
            "total_loans": sum(category_counts.values())
        }
        
    except Exception as e:
        logger.error(f"Error in local impairment staging: {str(e)}")
        db.rollback()
        raise
