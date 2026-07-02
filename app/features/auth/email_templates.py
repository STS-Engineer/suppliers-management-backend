"""HTML email templates for authentication flows."""

from app.core.config import settings

_BASE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<style>
  body {{ font-family: Arial, sans-serif; background: #f4f6f8; margin: 0; padding: 0; }}
  .wrapper {{ max-width: 560px; margin: 40px auto; background: #ffffff;
              border-radius: 8px; overflow: hidden;
              box-shadow: 0 2px 8px rgba(0,0,0,.08); }}
  .header {{ background: #1a3a5c; padding: 28px 32px; }}
  .header h1 {{ color: #ffffff; font-size: 20px; margin: 0; }}
  .body {{ padding: 32px; color: #333333; font-size: 15px; line-height: 1.6; }}
  .otp-box {{ background: #f0f4ff; border: 2px solid #3b6cb7; border-radius: 8px;
               text-align: center; padding: 20px; margin: 24px 0; }}
  .otp-box span {{ font-size: 36px; font-weight: bold; letter-spacing: 10px;
                   color: #1a3a5c; }}
  .btn {{ display: inline-block; background: #1a3a5c; color: #ffffff !important;
          text-decoration: none; padding: 13px 28px; border-radius: 6px;
          font-size: 15px; margin: 20px 0; font-weight: bold; }}
  .footer {{ background: #f4f6f8; padding: 16px 32px; font-size: 12px;
             color: #888888; text-align: center; }}
  p {{ margin: 0 0 14px; }}
</style>
</head>
<body>
<div class="wrapper">
  <div class="header"><h1>{app_name}</h1></div>
  <div class="body">{body}</div>
  <div class="footer">
    This is an automated message. Please do not reply to this email.
  </div>
</div>
</body>
</html>
"""


def _wrap(body: str) -> str:
    return _BASE.format(app_name=settings.APP_NAME, body=body)


def build_otp_email(full_name: str, otp: str, expire_minutes: int) -> str:
    body = f"""
<p>Hello {full_name},</p>
<p>We received a request to reset the password for your account.
Use the code below to proceed. It expires in <strong>{expire_minutes} minutes</strong>.</p>
<div class="otp-box"><span>{otp}</span></div>
<p>Enter this code on the password reset page to continue.</p>
<p>If you did not request a password reset, you can safely ignore this email.
Your password will not change.</p>
"""
    return _wrap(body)


def build_activation_email(
    full_name: str,
    activation_url: str,
    expire_hours: int,
    personal_message: str | None = None,
) -> str:
    extra = f"<p><em>{personal_message}</em></p>" if personal_message else ""
    body = f"""
<p>Hello {full_name},</p>
<p>Your account request has been <strong>approved</strong>. Welcome to Suppliers and purchasing management system !</p>
{extra}
<p>Click the button below to set your password and activate your account.
This link expires in <strong>{expire_hours} hours</strong>.</p>
<p style="text-align:center;">
  <a class="btn" href="{activation_url}">Activate My Account</a>
</p>
<p>Or copy and paste this link into your browser:</p>
<p style="word-break:break-all; font-size:13px; color:#555;">{activation_url}</p>
<p>If you did not request an account, please disregard this email.</p>
"""
    return _wrap(body)


def build_rejection_email(full_name: str, reason: str | None) -> str:
    reason_block = f"<p>Reason provided: <em>{reason}</em></p>" if reason else ""
    body = f"""
<p>Hello {full_name},</p>
<p>Thank you for your interest in {settings.APP_NAME}.</p>
<p>After reviewing your account request, we are unable to approve it at this time.</p>
{reason_block}
<p>If you believe this is a mistake or have any questions, please contact your
administrator directly.</p>
"""
    return _wrap(body)


def build_new_request_email(
    requester_name: str,
    requester_email: str,
    requested_role: str,
) -> str:
    body = f"""
<p>Hello,</p>
<p>A new account request has been submitted and requires your review.</p>
<table style="border-collapse:collapse; width:100%; font-size:14px;">
  <tr>
    <td style="padding:8px 12px; background:#f4f6f8; font-weight:bold; width:35%;">Name</td>
    <td style="padding:8px 12px;">{requester_name}</td>
  </tr>
  <tr>
    <td style="padding:8px 12px; background:#f4f6f8; font-weight:bold;">Email</td>
    <td style="padding:8px 12px;">{requester_email}</td>
  </tr>
  <tr>
    <td style="padding:8px 12px; background:#f4f6f8; font-weight:bold;">Requested role</td>
    <td style="padding:8px 12px;">{requested_role}</td>
  </tr>
</table>
<p style="margin-top:20px;">Please log in to the application to approve or reject this request.</p>
"""
    return _wrap(body)
