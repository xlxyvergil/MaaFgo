from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context


class MyCustomAction(CustomAction):

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:

        print("my_action_111 is running!")

        return True
