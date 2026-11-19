import logging
import os
from azure.identity import CertificateCredential
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

def get_access_token() -> str:
    """Get access token from Entra ID.

    Returns:
        str: Access token.

    Raises:
        RuntimeError: If any of the required environment variables are missing.
        azure.core.exceptions.ClientAuthenticationError: If Entra rejects the
            credential (e.g. invalid certificate, wrong tenant) or the token
            request otherwise fails. Propagated unchanged from the Azure SDK.
    """

    tenant_id = os.getenv("ENTRA_TENANT_ID")
    client_id = os.getenv("ENTRA_CLIENT_ID")
    certificate_data = os.getenv("CERTIFICATE_DATA")
    certificate_password = os.getenv("CERTIFICATE_PASSWORD")
    scope = os.getenv("ENTRA_SCOPE")

    missing = [
        name
        for name, value in (
            ("ENTRA_TENANT_ID", tenant_id),
            ("ENTRA_CLIENT_ID", client_id),
            ("CERTIFICATE_DATA", certificate_data),
            ("CERTIFICATE_PASSWORD", certificate_password),
            ("ENTRA_SCOPE", scope),
        )
        if not value
    ]
    if missing:
        # Log the misconfiguration before raising so a failed deploy leaves a
        # trace naming the missing field(s), not just an opaque stack trace.
        logger.error(
            "get_access_token: missing required Entra ID fields: %s",
            ", ".join(missing),
        )
        raise RuntimeError(f"Missing required Entra ID fields: {', '.join(missing)}")

    credential = CertificateCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        certificate_data=certificate_data.encode("utf-8") if isinstance(certificate_data, str) else certificate_data,
        password=certificate_password.encode("utf-8") if isinstance(certificate_password, str) else certificate_password,
    )

    logger.debug(
        "Requesting Entra access token (tenant_id=%s, client_id=%s)",
        tenant_id,
        client_id,
    )
    # Wrap the Azure call so an auth failure is logged distinctly here, rather
    # than surfacing only as a generic error at the search_business_apps
    # boundary. The exception is re-raised unchanged (identifiers only, never the
    # token or credential material are logged).
    try:
        token = credential.get_token(scope).token
    except Exception:
        logger.exception(
            "get_access_token: Entra token request failed (tenant_id=%s, client_id=%s)",
            tenant_id,
            client_id,
        )
        raise
    logger.info("Successfully obtained access token.")
    return token

if __name__ == "__main__":
    # FOR TESTING: fetch a token for manual use (e.g. pasting into Postman).
    # The raw token is a live credential, so copy it to the clipboard instead
    # of printing it to stdout where it would linger in terminal scrollback.
    #   python -m tools.business_apps_token
    import shutil
    import subprocess
    import sys

    try:
        access_token = get_access_token()
    except Exception as e:  # noqa: BLE001 - surface any failure to the operator
        print(f"Authentication failed: {e}")
    else:
        # clip on Windows, pbcopy on macOS (ecm uses pbcopy only).
        command = ["clip"] if sys.platform == "win32" else ["pbcopy"]
        copied = False
        if shutil.which(command[0]):
            try:
                subprocess.run(command, input=access_token, text=True, check=True)
                copied = True
            except (OSError, subprocess.SubprocessError):
                copied = False

        if copied:
            print(f"Access token copied to clipboard (length={len(access_token)}).")
        else:
            # Unlike ecm, never fall back to printing the token.
            print("Could not copy to clipboard; the token was NOT printed.")
