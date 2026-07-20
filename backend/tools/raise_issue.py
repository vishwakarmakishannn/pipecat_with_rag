import re
import asyncio
from pipecat.services.llm_service import FunctionCallParams
from core.database import VoiceSessionLocal
from core.models import Issue
from core.tool_config import tool_timeout_seconds

async def raise_issue(
    params: FunctionCallParams,
    cust_id: str,
    email: str,
    mobile: str,
    device_id: str,
    description: str
):
    """Raise a complaint issue and save it to the database.
    
    Args:
        cust_id: Customer ID. Must start with 'C' followed by 6 digits (e.g. C123456).
        email: Customer's email address.
        mobile: Customer's mobile number. Must be a 10-digit Indian number starting with 6, 7, 8, or 9 (exclude +91).
        device_id: Device ID. Must start with 'MSW' followed by 8 digits (e.g. MSW12345678).
        description: A brief description of the issue.
    """
    errors = []
    
    if not re.match(r"^C\d{6}$", cust_id):
        errors.append("Invalid cust_id format. Must start with 'C' followed by 6 digits.")
        
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        errors.append("Invalid email format.")
        
    if not re.match(r"^[6-9]\d{9}$", mobile):
        errors.append("Invalid mobile format. Must be a 10-digit number starting with 6, 7, 8, or 9.")
        
    if not re.match(r"^MSW\d{8}$", device_id):
        errors.append("Invalid device_id format. Must start with 'MSW' followed by 8 digits.")
        
    if errors:
        error_msg = "Validation failed: " + "; ".join(errors) + " Please ask the user for correct information."
        await params.result_callback({"status": "error", "message": error_msg})
        return

    try:
        async with asyncio.timeout(tool_timeout_seconds()):
            async with VoiceSessionLocal() as session:
                new_issue = Issue(
                    cust_id=cust_id,
                    email=email,
                    mobile=mobile,
                    device_id=device_id,
                    description=description
                )
                session.add(new_issue)
                await session.commit()
                await session.refresh(new_issue)
    except TimeoutError:
        await params.result_callback({
            "status": "timeout",
            "message": "Issue creation timed out and was not confirmed. Ask the user to retry later.",
        })
        return
    except asyncio.CancelledError:
        raise
    except Exception:
        await params.result_callback({
            "status": "error",
            "message": "Issue creation failed and was not confirmed. Ask the user to retry later.",
        })
        return
    
    await params.result_callback({
        "status": "success",
        "message": f"Issue #{new_issue.id} has been successfully raised."
    })
