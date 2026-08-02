import json
from os.path import sep as path_sep
from langchain_openai import ChatOpenAI
from langchain.tools import tool
from langchain_classic.agents import create_openai_tools_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_classic.memory import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory


class Agent:

    def __init__(self, api_key, model, ft_model=None, base_url=None, sch_tools=[], info_tools=[], temperature=0):
        # Memory store for user sessions
        self.store = {}

        # Load the base llm
        self.llm = ChatOpenAI(
            api_key=api_key,
            model=model,
            base_url=base_url,
            temperature=temperature
        )

        # The exit advisor needs to be fine_tuned
        # Load the fine tuned llm
        # use normal model for testing purposes only
        self.ft_llm = ChatOpenAI(
            api_key=api_key,
            model=ft_model if ft_model is not None else model,
            base_url=base_url,
            temperature=temperature
        )

        # Advisors store
        self.advisors = {
            'info': self.build_agent(self.llm,f"agents_Instructions{path_sep}info_advisor.md", tools=info_tools, verbose=True),
            'schedule': self.build_agent(self.llm,f"agents_Instructions{path_sep}schedule_advisor.md", tools=sch_tools, verbose=True),
            'exit': self.build_agent(self.ft_llm, f"agents_Instructions{path_sep}exit_advisor.md", verbose=True)
        }

        self.session = None

        @tool
        def choose_advisor(advisor: str, instruction: str = "") -> str:
            """
            ## Advisor parameter
            Select the appropriate advisor to consult.
            Choose one of:
            * Info
            * Schedule
            * Exit
            
            ## Instruction parameter (Optional)
            Additional instructions sent to the advisor to help direct it.

            ## Returns
            This tool will return The **output** of the selected advisor.
            The output is structured as a json formatted string.
            If the selected advisor does not match one of the above advisors,
            the tool will return an appropriate message.            

            ## Examples
            Example 1 Input:
            - {'advisor':'Info'}

            Example 1 Output:
            - {"Info Advisor": "Hello! How can I help you today?"}

            Example 2 Input:
            - {'advisor':'Schedule', 'instruction': 'The user wants to schedule an interview in 2024-09-02'}

            Example 2 Output:
            - {"Schedule Advisor": "Certainly, I can help you with that. Here are the available Time slots available for 2024-09-02... "}

            Example 3 Input:
            - {'advisor':'Exit'}

            Example 3 Output:
            - {"Exit Advisor": "Sounds like the user is not interested anymore. You can end the conversation as follows..."}

            """

            try:
                if self.session is None:
                    raise Exception("Session Loading Failed")

            # Retrieve full history from memory
                full_history = self.store[self.session].messages
                if len(instruction) < 1:
                    # No instructions provided
                    full_convo = "\n".join([f"{m.type.capitalize()}: {m.content}" for m in full_history])
                else:
                    full_convo = "\n".join([f"{m.type.capitalize()}: {m.content}" for m in full_history]+[f"main_agent: {instruction}"])
                # return advisor's output instead of just indication
                advisor_output = self.advisors[advisor.lower()].invoke({"input": full_convo})["output"]
                #return f'Advisor {advisor} has been selected. You are now outputting to the selected advisor.'
                return json.dumps({f'{advisor} Advisor': advisor_output})
            except KeyError:
                return "No such advisor found, please make sure to select from the advisors described in the tool description."
            except Exception as e:
                return str(e)

        self.main_agent = self.build_agent(self.llm,f"agents_Instructions{path_sep}main_agent.md", tools=[choose_advisor],has_history=True, verbose=True)

    def step(self, session_id, user_input):
        """
        Handles one turn of user input for the main agent (with memory),
        and if needed, passes the full memory/history to the advisor agents.
        Then follows it up with a summarizing message for the user.
        """
        self.session = session_id
        # Main agent receives latest user message (memory auto-injects context)
        main_output = self.main_agent.invoke({"input": user_input},config={"configurable": {"session_id": session_id}})["output"]
        print("Main Agent:", main_output)
        print("\n")          

    # retreive session history
    def get_history(self,session_id):
        if session_id not in self.store:
            self.store[session_id] = ChatMessageHistory()
        return self.store[session_id]
    
    # create the agent pipeline
    def build_agent(self, llm, context_file, tools=[], has_history=False, verbose=False):
        # load context
        with open(context_file) as f:
            context = f.read()

        # Create the messages list
        messages = [
            ("system", context),
            MessagesPlaceholder(variable_name="history"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
            ("user", "{input}")
        ]

        # remove history if not needed
        if not has_history:
            messages.pop(1)

        # Create the Prompt template
        prompt = ChatPromptTemplate.from_messages(messages)

        # Build agent
        agent = create_openai_tools_agent(llm, tools=tools, prompt=prompt)
        executor = AgentExecutor(agent=agent, tools=tools, verbose=verbose)

        # return with or without history
        if has_history:
            return RunnableWithMessageHistory(
                executor,
                get_session_history=self.get_history,
                input_messages_key="input",
                history_messages_key="history"
            )
        else:
            return executor







