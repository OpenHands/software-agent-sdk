"""Field-level opt-out marker for :mod:`openhands.sdk.conversation.secret_registry`.

Lives in ``sdk.utils`` so both the masker and the models it walks (which the
masker's package imports transitively) can reference it without a cycle.
"""


class SkipSecretMasking:
    """Marker for fields whose contents must not be scanned for secrets.

    Attach with ``Annotated[T, SkipSecretMasking()]`` on any Pydantic field
    that carries an opaque payload where a substring ``.replace`` would
    corrupt the data — base64 blobs, hashes, signed tokens, other binaries.
    :func:`openhands.sdk.conversation.secret_registry._mask_model` sees the
    marker and leaves the field untouched.

    Masking is a string-level ``replace``: a short registered secret whose
    characters happen to appear inside a large opaque blob (screenshots are
    hundreds of KB of base64 and collide with any 8+ char token by chance)
    would otherwise get ``<secret-hidden>`` spliced mid-payload, and a
    downstream provider that validates the blob will reject the whole turn.
    """

    __slots__ = ()
