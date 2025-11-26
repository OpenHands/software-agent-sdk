from collections.abc import Callable

from litellm.types.utils import ModelResponseStream


TokenCallbackType = Callable[[ModelResponseStream], None]
