"""Domain-level service errors."""


class ServiceError(Exception):
    """Base class for service-layer errors."""

    status_code = 500

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NotFoundError(ServiceError):
    """Requested entity was not found."""

    status_code = 404


class ConflictError(ServiceError):
    """Requested operation conflicts with current state."""

    status_code = 409


class UnauthorizedError(ServiceError):
    """Authentication or authorization failed."""

    status_code = 401


class BadRequestError(ServiceError):
    """Request payload is semantically invalid."""

    status_code = 400
