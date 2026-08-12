import json
from os.path import sep as path_sep
from langchain_openai import ChatOpenAI
from langchain.tools import tool
from langchain_classic.agents import create_openai_tools_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_classic.memory import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory


class Agent:

    def __init__(self, api_key, model, ft_model=None, base_url=None, system_message="", sch_tools=[], info_tools=[], temperature=0, verbose=False):
        # Memory store for user sessions
        self.store = {}
        # set verbose 
        self.verbose = verbose
        # Optional system message for all agents
        self.system_message = system_message

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
            'info': self.build_agent(self.llm,f"agents_Instructions{path_sep}info_advisor.md", tools=info_tools),
            'schedule': self.build_agent(self.llm,f"agents_Instructions{path_sep}schedule_advisor.md", tools=sch_tools),
            'exit': self.build_agent(self.ft_llm, f"agents_Instructions{path_sep}exit_advisor.md")
        }

        self.session = None

        @tool
        def choose_advisor(advisor: str, prompt: str = "") -> dict:
            """
            ## Advisor parameter
            Select the appropriate advisor to consult.
            Choose one of:
            * Info
            * Schedule
            * Exit
            
            ## Prompt parameter (Optional)
            Could be anything from a question to an instruction, depending on the situation, that is sent to the Advisor.

            ## Returns
            This tool will return The **output** of the selected advisor.
            The output is structured as a dicttionary.
            If the selected advisor does not match one of the above advisors,
            the tool will return an appropriate message.

            ## Examples
            Example 1 Input:
            - {'advisor':'Info'}

            Example 1 Output:
            - {"Info Advisor": "Hello! How can I help you today?"}

            Example 2 Input:
            - {'advisor':'Schedule', 'prompt': 'The user wants to schedule an interview in 2024-09-02'}

            Example 2 Output:
            - {"Schedule Advisor": "Certainly, I can help you with that. Here are the available Time slots available for 2024-09-02... "}

            Example 3 Input:
            - {'advisor':'Exit', 'prompt': "The user started talking about his troubles in life, should we end the conversation?"}

            Example 3 Output:
            - {"Exit Advisor": "Sounds like the user is not interested in applying to the position anymore. You can end the conversation as follows..."}

            """

            try:
                if self.session is None:
                    raise Exception("Session Loading Failed")

            # Retrieve full history from memory
                full_history = self.get_from_store(self.session).messages
                # log the intention of the main agent in each step
                self.get_from_store(self.session, hist=False).append('continue' if advisor.lower() == 'info' else advisor.lower())
                if len(prompt) < 1:
                    # No instructions provided
                    full_convo = "\n".join([f"{m.type.capitalize()}: {m.content}" for m in full_history])
                else:
                    full_convo = "\n".join([f"{m.type.capitalize()}: {m.content}" for m in full_history]+[f"Main Agent: {prompt}"])
                # return advisor's output instead of just indication
                advisor_output = self.advisors[advisor.lower()].invoke({"input": full_convo})["output"]
                #return f'Advisor {advisor} has been selected. You are now outputting to the selected advisor.'
                return {f'{advisor} Advisor': advisor_output}
            except KeyError:
                return {"Error": "No such advisor found, please make sure to select from the advisors described in the tool description."}
            except Exception as e:
                return {"Error":str(e)}

        self.main_agent = self.build_agent(self.llm,f"agents_Instructions{path_sep}main_agent.md", tools=[choose_advisor],has_history=True)

    def step(self, session_id, user_input="") -> str:
        """
        Handles one turn of user input for the main agent (with memory),
        and if needed, passes the full memory/history to the advisor agents.
        Then follows it up with a summarizing message for the user.
        """
        self.session = session_id
        # Main agent receives latest user message (memory auto-injects context)
        return self.main_agent.invoke({"input": user_input},config={"configurable": {"session_id": session_id}})["output"] 

    # retreive session history or logges
    def get_from_store(self,session_id, hist=True):
        if session_id not in self.store:
            # adding intention logger for evaluation
            self.store[session_id] = {'hist': ChatMessageHistory(), 'int_log': []}
        return self.store[session_id]['int_log'] if not hist else self.store[session_id]['hist']
    
    # create the agent pipeline
    def build_agent(self, llm, context_file, tools=[], has_history=False):
        # load context
        with open(context_file) as f:
            context = f.read()

        # handle the system message
        context += f'\n\n##Additional Info From The App\n{self.system_message}'

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
        executor = AgentExecutor(agent=agent, tools=tools, verbose=self.verbose)

        # return with or without history
        if has_history:
            return RunnableWithMessageHistory(
                executor,
                get_session_history=self.get_from_store,
                input_messages_key="input",
                history_messages_key="history"
            )
        else:
            return executor




if __name__ == '__main__':
    import os
    from dotenv import load_dotenv
    from app.modules.embedding import build_search_job_description_tool, Embedder

    load_dotenv()

    from datetime import datetime, timedelta

    #Creating the function
    @tool
    def get_next_three_dates(start_date):
        'This fuction recieves a date and then return 3 optional dates'
        # start_date should be a string in the format 'YYYY-MM-DD'
        date_obj = datetime.strptime(start_date, "%Y-%m-%d")
        return [
            (date_obj + timedelta(days=3)).strftime("%Y-%m-%d"),
            (date_obj + timedelta(days=6)).strftime("%Y-%m-%d"),
            (date_obj + timedelta(days=9)).strftime("%Y-%m-%d"),
        ]

    print(os.listdir())

    # Create an Embedding db if not found:
    if 'chroma_db' not in os.listdir():
        print("Creating chroma_db")
        embed = Embedder()
        embed.build_vectorstore('../Python Developer Job Description.pdf')

    # Create the info tool function
    load_info = build_search_job_description_tool()

    # Can use either ollama or openai models, whatever is preferred.
    # Just notice that when using openai llm, base_url param is optional.
    # using ollama chat llm: gemma4:e4b, for testing only.
    a1 = Agent(
        api_key="ollama",
        model="gemma4:e4b", 
        base_url="http://localhost:11434/v1",
        system_message="The User's application has been received successfully. The user will be redirected to you.",
        sch_tools=[get_next_three_dates],
        info_tools=[load_info],
        temperature = 0,
        verbose=True
    )

    # Step usage
    a1.step("user1")
    print(a1.get_from_store(a1.session))
    a1.step("user1", "I've been using Python professionally for five years, mostly for ML.")
    print(a1.get_from_store(a1.session))
    # a1.step("user1", "I can't at that time—I'm busy.")
    # a1.step("user1", "I'm sorry, but I'm no longer interested.")