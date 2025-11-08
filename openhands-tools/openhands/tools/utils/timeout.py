from func_timeout import FunctionTimedOut, func_timeout


class TimeoutError(Exception):
    """Generic SDK Tool TimeoutError (wraps func-timeout)."""

    pass


def run_with_timeout(func, timeout, *args, **kwargs):
    try:
        return func_timeout(timeout, func, args=args, kwargs=kwargs)
    except FunctionTimedOut:
        raise TimeoutError(f"Operation timed out after {timeout} seconds")
