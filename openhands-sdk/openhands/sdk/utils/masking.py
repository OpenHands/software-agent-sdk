"""Field metadata used by secret masking."""


class SkipSecretMasking:
    """Mark a field whose value must not be secret-masked."""

    __slots__ = ()


class PreserveDataUrls:
    """Mark string values that contain opaque base64 data URLs."""

    __slots__ = ()
