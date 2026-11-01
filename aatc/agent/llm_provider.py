"""Provider-neutral chat interface used by the traffic agent."""


class ProviderError(RuntimeError):
    pass


class LLMProvider:
    name = "abstract"

    def complete(self, messages, tools):
        raise NotImplementedError
