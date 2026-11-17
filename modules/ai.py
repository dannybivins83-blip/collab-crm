# -*- coding: utf-8 -*-
"""One place that knows how to call the Anthropic SDK safely.

Both the proposal reader and the takeoff extractor call messages.create with
the same shape. When the unpinned SDK changed its signature, both broke the
same way on the same day. They now go through one function so the next
signature change is one fix, not a hunt.
"""
def anthropic_create(client, **kwargs):
    """Call messages.create with only the arguments this SDK actually accepts.

    requirements.txt pins `anthropic>=0.30.0`, so Render installs whatever is
    newest at build time. A newer SDK dropped `temperature` from the signature
    and every call passing it began failing with

        TypeError: Messages.create() got an unexpected keyword argument 'temperature'

    which is a Python signature error, so it happens before any request is made
    and looks nothing like an API problem. It broke the proposal reader and the
    takeoff extractor at the same time, silently, on a dependency nobody
    changed. Dropping an unsupported argument is better than losing the whole
    call: `temperature=0` is a nicety for extraction, the tool schema is what
    actually forces the shape of the answer.
    """
    import inspect
    try:
        params = inspect.signature(client.messages.create).parameters
    except Exception:
        return client.messages.create(**kwargs)
    # A **kwargs catch-all may be named anything, so detect it by KIND. Checking
    # for the literal name "kwargs" wrongly concluded the wrapper took no
    # keywords and stripped every argument, which is worse than the bug it was
    # written to fix.
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return client.messages.create(**kwargs)
    for k in [k for k in kwargs if k not in params]:
        kwargs.pop(k, None)
    return client.messages.create(**kwargs)
