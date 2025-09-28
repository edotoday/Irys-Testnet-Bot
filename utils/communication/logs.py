from models import OperationResult


def operation_failed(pk_or_mnemonic: str, data: dict = None) -> OperationResult:
    return OperationResult(
        pk_or_mnemonic=pk_or_mnemonic,
        data=data,
        status=False,
    )


def operation_success(pk_or_mnemonic: str, data: dict = None) -> OperationResult:
    return OperationResult(
        pk_or_mnemonic=pk_or_mnemonic,
        data=data,
        status=True,
    )


def validate_error(error: Exception) -> str:
    error_message = str(error).lower()

    if "operation timed out after" in error_message:
        return "Server didn't respond in time"

    elif (
        "curl: (7)" in error_message
        or "curl: (28)" in error_message
        or "curl: (16)" in error_message
        or "connect tunnel failed" in error_message
    ):
        return "Proxy failed" if "connect tunnel failed" not in error_message else f"Connection failed: {error_message}"

    elif "timed out" in error_message or "operation timed out" in error_message:
        return "Connection timed out"

    elif "empty document" in error_message or "expecting value" in error_message:
        return "Received empty response"

    elif (
            "curl: (35)" in error_message
            or "curl: (97)" in error_message
            or "eof" in error_message
            or "curl: (56)" in error_message
            or "ssl" in error_message
    ):
        return "SSL Error. If there are a lot of such errors, try installing certificates."

    elif "417 Expectation Failed" in error_message:
        return "417 Expectation Failed"

    elif "unsuccessful tunnel" in error_message:
        return "Unsuccessful TLS Tunnel"

    elif "connection error" in error_message:
        return "Connection Error"

    else:
        return error_message
