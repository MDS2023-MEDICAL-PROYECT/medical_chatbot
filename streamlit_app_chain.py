import time
import json
import langchain
import streamlit as st
import datetime

from langchain import PromptTemplate, LLMChain, OpenAI
from langchain.retrievers import PineconeHybridSearchRetriever
from pinecone_text.sparse import BM25Encoder
from langchain.vectorstores.base import VectorStoreRetriever
import database as db

from pathlib import Path
from PIL import Image
from streamlit_chat import message
from langchain.chains import ConversationalRetrievalChain, RetrievalQA
from constants import OPENAI_API_KEY, INDEX_NAME, PINECONE_API_ENV, PINECONE_API_KEY, ITERATIONS
from langchain.embeddings import OpenAIEmbeddings
from langchain.vectorstores import Pinecone
from langchain.chat_models import ChatOpenAI
import pinecone
from langchain.chains.conversation.memory import ConversationBufferMemory


def clean_string(input):
    substring = ""

    if input is not None:
        start_pos = str(input).find(" additional_kwargs")
        if start_pos != -1:
            substring = str(input)[8:start_pos]

    return substring


def convert_to_string(messages):
    i = 0
    result = ""
    for message in messages:
        if i % 2 == 0:
            result += "-Human: " + clean_string(message) + "\n"
        else:
            result += "-Doctor: " + clean_string(message) + "\n"
        i = i + 1

    return result


def init_vectorstore():
    pinecone.init(
        api_key=PINECONE_API_KEY,  # find at app.pinecone.io
        environment=PINECONE_API_ENV,  # next to api key in console
    )
    index_name = INDEX_NAME

    if index_name not in pinecone.list_indexes():
        pinecone.create_index(INDEX_NAME, dimension=1536, metric="euclidean")

    index = pinecone.Index(index_name)
    return index


def get_vectordb():
    index = init_vectorstore()
    vectordb = PineconeHybridSearchRetriever(
            embeddings=OpenAIEmbeddings(
                        model="text-embedding-ada-002",
                        openai_api_key=OPENAI_API_KEY)
            , sparse_encoder=BM25Encoder().default(), index=index, top_k=10, alpha=0
            )
    return vectordb


def get_conversation_chain(vectordb):
    llm = ChatOpenAI(temperature=0)
    retriever = vectordb

    if "memory" not in st.session_state:
        st.session_state.memory = ConversationBufferMemory(memory_key='chat_history', return_messages=True)

    template = """As a virtual medical assistant, your role is to facilitate a detailed understanding of the 
    patient's current health condition. Refer to the conversation history to identify the symptoms explicitly 
    mentioned by the patient so far. Then, with the help of the relevant medical texts — which contain information 
    about symptoms in other patients — formulate a single follow-up question to inquire about a related symptom that 
    the patient has not mentioned but is present in the medical texts, helping in gathering comprehensive details to 
    understand the patient's health better.

    Please adhere to the following guidelines:
    - Pose only one follow-up question in each interaction to maintain a focused and fruitful conversation.
    - Ask only about one specific symptom
    - Formulate your question from a second-person perspective to foster a direct and personalized engagement with the patient.
    - Avoid making explicit references to the medical texts in your questions to maintain a natural conversation flow.
    - Refrain from referring to specific details from the medical texts to avoid confusion.
    - Steer clear of repeating questions that have previously been posed during the conversation.
    - Be very kind
    
    **Your Previous Responses**: {question} ----------- **Relevant Medical Texts**: {context} -----------"""

    _template = """Given the following conversation and a follow up question, rephrase the follow up question to be a standalone question, in its original language.

        Chat History:
        {chat_history}
        Follow Up Input: {question}
        Standalone question:"""

    QA_PROMPT = PromptTemplate(template=template, input_variables=["question", "context"])
    CONDENSE_QUESTION_PROMPT = PromptTemplate.from_template(_template)

    model = ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=retriever,
        memory=st.session_state.memory,
        condense_question_llm=ChatOpenAI(temperature=0, model='gpt-3.5-turbo'),
        condense_question_prompt=CONDENSE_QUESTION_PROMPT,
        combine_docs_chain_kwargs={"prompt": QA_PROMPT})

    return model


def get_diagnosis(vectordb, symptoms):
    llm_diagnosis = OpenAI(temperature=0)
    retriever = vectordb

    diagnosis_template = """Given the following symptoms of a patient known as human and the knowledge given in the Relevant 
    Medical texts, give a 3 possible diagnosis and a number from 0 to 100 with the confidence level you have in the diagnosis.
    Followed by the medical specialist the patient should visit given the diagnosis.
    Give the diagnosis in the following format:
    [{{"diagnosis": "diagnosis1","confidence level": 90,"specialist": "specialist1"}},{{"diagnosis": "diagnosis2","confidence level": 70,"specialist": "specialist2"}},{{"diagnosis": "diagnosis3","confidence level": 65,"specialist": "specialist3"}}]

    Human symptoms: {question} ----------- Relevant Medical Texts: {context} -----------Give the diagnosis:"""

    DIAGNOSIS_PROMPT = PromptTemplate(template=diagnosis_template, input_variables=["question", "context"])

    diagnosis_model = RetrievalQA.from_chain_type(
        llm=llm_diagnosis,
        retriever=retriever,
        return_source_documents=True,
        chain_type_kwargs={"prompt": DIAGNOSIS_PROMPT})

    return diagnosis_model(symptoms)["result"]


def get_symptoms_summary(chat_history):
    llm = ChatOpenAI(temperature=0)
    prompt_template = PromptTemplate.from_template(
        """Given the following chat history: 
        *****Chat history between Human and Assistant*****
        {chat}
        *****
        Summarize the Human symptoms"""
    )
    prompt_template.format(chat=chat_history)
    summary_model = LLMChain(llm=llm, prompt=prompt_template, output_key="summary")

    return summary_model(chat_history)


def create_medical_report(chat_history):
    llm = ChatOpenAI(temperature=0)
    prompt_report_template = PromptTemplate.from_template(
        """Given the following chat history: 
        *****Chat history between Human and Assistant*****
        {chat}
        *****
        Generate the human medical report"""
    )
    prompt_report_template.format(chat=chat_history)
    report_model = LLMChain(llm=llm, prompt=prompt_report_template, output_key="report")

    return report_model(chat_history)


def main():
    langchain.debug = True
    # initial session_state in order to avoid refresh

    if "vectordb" not in st.session_state:
        st.session_state.vectordb = get_vectordb()
    if "index" not in st.session_state:
        st.session_state.index = init_vectorstore()
    if "conversation_chain" not in st.session_state:
        st.session_state.conversation_chain = get_conversation_chain(st.session_state.vectordb)
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "authenticator" not in st.session_state:
        st.session_state.authenticator = None
    if "name" not in st.session_state:
        st.session_state.name = None
    if "dni" not in st.session_state:
        st.session_state.dni = None
    if "iterations" not in st.session_state:
        st.session_state.iterations = 0
    if "summary_symptoms" not in st.session_state:
        st.session_state.summary_symptoms = ""

    # Sidebar for personal information
    with st.sidebar:
        current_dir = Path(__file__).parent if "__file__" in locals() else Path.cwd()
        profile_pic_path = current_dir / "assets" / "Bernardo.png"
        profile_pic = Image.open(profile_pic_path)
        patient = db.get_patient(st.session_state.dni)

        st.sidebar.header("Your Information")
        st.session_state.authenticator.logout('Logout', 'sidebar', key='unique_key')
        st.image(profile_pic, width=250)

        st.markdown(f"""
                **Nombre**: {st.session_state.name}\n
                **DNI**: {st.session_state.dni}\n  
                **Fecha de Nacimiento**: 15 de Agosto de 1983\n  
                **Sexo**: Masculino\n  
                **Dirección**: Calle Real No. 45, Ciudad Central\n  
                **Teléfono**: +34630547119\n
                """)

        next_appointment = datetime.date(2023, 9, 28)
        next_appointment_2 = datetime.date(2023, 10, 30)

        with st.expander("Next Appointments", expanded=True):
            st.date_input(label="X-Ray", value=next_appointment, disabled=False, format="DD/MM/YYYY")

    personal_message = f"Hello {st.session_state.name}, Hello! I'm here to assist you in finding the right specialist for your health concerns. Can you tell me about the symptoms you're experiencing?"
    message(personal_message, is_user=False, avatar_style="big-smile")

    finish_conversation = st.session_state.iterations >= ITERATIONS
    user_question = st.chat_input(placeholder="Describe your symptoms", disabled=finish_conversation)

    if user_question:
        if not st.session_state.chat_history:
            pass
        else:
            for i, msg in enumerate(st.session_state.chat_history):
                if i % 2 == 0:
                    message(msg.content, is_user=True, key=str(i) + '_user', avatar_style="personas")
                else:
                    message(msg.content, is_user=False, key=str(i) + '_ai', avatar_style="big-smile")

        message(user_question, is_user=True, key=str(datetime.datetime.now()) + '_user', avatar_style="personas")

        if not finish_conversation:
            with st.spinner("Thinking..."):
                st.session_state.responses = st.session_state.conversation_chain({'question': user_question})
                message(st.session_state.responses['answer'], is_user=False, key=str(datetime.datetime.now()) + '_ai',
                        avatar_style="big-smile")
                st.session_state.chat_history = st.session_state.responses['chat_history']
                st.session_state.summary_symptoms = get_symptoms_summary(str(st.session_state.chat_history))
                st.write(st.session_state.summary_symptoms["summary"])

        else:
            with st.status("Searching your appointment ...", expanded=True) as status:
                st.write('Processing your symptoms to find a specialist...')
                diagnosis = get_diagnosis(st.session_state.vectordb, st.session_state.summary_symptoms["summary"])
                diagnosis_list = json.loads(diagnosis)
                specialist = diagnosis_list[0]["specialist"]
                # time.sleep(4)
                st.write(f'We recommend you to take an appointment with a {specialist}. Looking in his agenda ...')
                medical_report = create_medical_report(st.session_state.chat_history)["report"]
                db.update_patient(updates={"diagnosis": diagnosis}, dni=st.session_state.dni)
                db.update_patient(updates={"report": medical_report}, dni=st.session_state.dni)
                st.date_input(label=f"{specialist}", value=next_appointment_2, disabled=False, format="DD/MM/YYYY")
                status.update(label="Appointment found!", state="complete", expanded=True)
                st.button("Take Appointment")

        # number of interactions with the patient
        st.session_state.iterations += 1
