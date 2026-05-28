import streamlit as st
import pandas as pd
import duckdb
import plotly.express as px
import os

DB_PATH = "caminho do seu db.duckdb"  # Atualize para o caminho do seu banco de dados local

def get_db_connection():
    return duckdb.connect(DB_PATH, read_only=True)

def get_schema_info():
    try:
        conn = get_db_connection()
        tabelas = conn.execute("SHOW TABLES").fetchdf()
        info = "ESQUEMA DO BANCO DE DADOS (DuckDB):\n"
        for tbl in tabelas['name']:
            cols = conn.execute(f"PRAGMA table_info('{tbl}')").fetchdf()
            col_list = [f"{r['name']} ({r['type']})" for _, r in cols.iterrows()]
            info += f"- Tabela: `{tbl}` | Colunas: {', '.join(col_list)}\n"
        info += ("\nNota: Para consultas, prefira utilizar a 'view_analitica_sih' se existir, ou faça JOIN "
                 "entre 'internacoes', 'hospital' e 'especialidade'. Lembre-se que as regras devem ser 100% "
                 "baseadas nestes dados. Você será penalizado se inventar informações externas.")
        return info
    except Exception as e:
        return f"Erro ao ler esquema: {e}"

def executar_consulta_sql(query_sql: str) -> str:
    """Executa uma consulta SQL no banco local DuckDB e retorna os dados em formato tabular. Útil para extrair valores exatos."""
    try:
        conn = get_db_connection()
        df = conn.execute(query_sql).fetchdf()
        if df.empty:
            return "A consulta não retornou nenhum dado."
        return df.head(100).to_string()
    except Exception as e:
        return f"Erro na consulta SQL: {e}"

def criar_dashboard_grafico(query_sql: str, tipo_grafico: str, titulo: str, coluna_x: str, colunas_y: list[str]) -> str:
    """
    Gera um dashboard com um gráfico a partir da consulta SQL, exibindo-o diretamente ao usuário.
    'tipo_grafico': pode ser 'bar' (barras), 'line' (linha) ou 'pie' (pizza).
    'query_sql': consulta SQL para buscar os dados.
    'coluna_x': nome da coluna que ficará no eixo X.
    'colunas_y': lista com o nome da(s) coluna(s) do eixo Y.
    """
    try:
        conn = get_db_connection()
        df = conn.execute(query_sql).fetchdf()
        if df.empty:
            return "Sem dados para plotar o dashboard."
            
        fig = None
        if tipo_grafico == 'bar':
            fig = px.bar(df, x=coluna_x, y=colunas_y, title=titulo, barmode='group')
        elif tipo_grafico == 'line':
            fig = px.line(df, x=coluna_x, y=colunas_y, title=titulo, markers=True)
        elif tipo_grafico == 'pie':
            fig = px.pie(df, names=coluna_x, values=colunas_y[0], title=titulo)
        else:
            return "Tipo de gráfico inválido."
        
        # Armazena na sessão para a interface desenhar
        if "agent_figs" not in st.session_state:
            st.session_state.agent_figs = []
            
        st.session_state.agent_figs.append({"fig": fig, "title": titulo})
        return f"Gráfico elaborado e enviado ao dashboard da interface."
    except Exception as e:
        return f"Erro ao criar gráfico: {e}"


@st.dialog("Chat com AgenteIA", width="large")
def chat_agent():
    st.markdown("### Bem-vindo ao AgenteIA 🤖")
    st.info("""
    **Limites de Atuação:** 100% baseado no Banco de Dados (SIH/DUCKDB). Não invento fontes nem integro dados externos.
    Se a resposta exigir dados que não possuo, serei honesto. Posso criar Dashboards em tempo real para as perguntas.
    *(Certifique-se de preencher a GEMINI_API_KEY no menu Settings/Ambiente ou no arquivo .streamlit/secrets.toml)*
    """)
    
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key and hasattr(st, "secrets") and "GEMINI_API_KEY" in st.secrets:
        api_key = st.secrets["GEMINI_API_KEY"]
        
    try:
        from google import genai
        from google.genai import types
        HAS_GENAI = True
    except ImportError:
        HAS_GENAI = False

    if not HAS_GENAI:
        st.error("⚠️ O pacote `google-genai` não está instalado. Adicione ao ambiente via: `pip install google-genai`.")
        return
        
    if not api_key:
        st.warning("⚠️ Chave de API do Gemini não localizada. Por favor, configure `GEMINI_API_KEY` para testar o agente.")
        return

    # Inicializa estado do chat e do Client
    if "gemini_chat_agent" not in st.session_state:
        client = genai.Client(api_key=api_key)
        st.session_state.gemini_client = client
        sys_inst = (
            "Você é o AgenteIA, analista de dados e arquitetura hospitalar focado no SUS. "
            "Você TEM OBRIGAÇÃO de utilizar a ferramenta 'executar_consulta_sql' para recuperar dados antes de responder a QUALQUER pergunta do usuário. "
            "Nunca assuma que sabe a resposta. Consulte os dados ativamente pelo schema fornecido. "
            "Se o usuário pedir um gráfico, invoque a ferramenta 'criar_dashboard_grafico' obrigatoriamente. "
            "Seja preciso e direto nas respostas. Apenas se os dados realmente não existirem no DuckDB após a execução da consulta, utilize a frase de fallback: "
            "'O questionamento transpassa os limites definidos para busca e informações presentes no banco de dados.'\n\n"
            f"{get_schema_info()}"
        )
        
        config = types.GenerateContentConfig(
            system_instruction=sys_inst,
            tools=[executar_consulta_sql, criar_dashboard_grafico],
            temperature=0.1
        )
        
        st.session_state.gemini_chat_agent = client.chats.create(model="gemini-2.5-flash", config=config)
        st.session_state.agent_messages = []
        
    if "agent_figs" not in st.session_state:
        st.session_state.agent_figs = []

    # Display Message History
    for msg in st.session_state.agent_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            # Display charts if any attached to this message
            if "figs" in msg and msg["figs"]:
                for f in msg["figs"]:
                    st.plotly_chart(f, use_container_width=True)

    # Chat Input
    if prompt := st.chat_input("Ex: Qual a cidade com a maior evasão hospitalar em 2023?"):
        
        # Limpa as figuras em fila
        st.session_state.agent_figs = []
        
        # Adiciona a msgs ui
        st.session_state.agent_messages.append({"role": "user", "content": prompt, "figs": []})
        with st.chat_message("user"):
            st.markdown(prompt)
            
        with st.chat_message("assistant"):
            with st.spinner("Analisando dados (executando SQL e/ou Agentes)..."):
                try:
                    # Envia prompt pro modelo (as tools podem ser acionadas e agendadas em st.session_state.agent_figs)
                    response = st.session_state.gemini_chat_agent.send_message(prompt)
                    resposta_texto = response.text
                    
                    st.markdown(resposta_texto)
                    # Plota gráficos que a ferramenta possa ter enfileirado
                    figuras_geradas = []
                    for item in st.session_state.agent_figs:
                        st.plotly_chart(item["fig"], use_container_width=True)
                        figuras_geradas.append(item["fig"])
                        
                    st.session_state.agent_messages.append({
                        "role": "assistant", 
                        "content": resposta_texto,
                        "figs": figuras_geradas
                    })
                    
                except Exception as e:
                    st.error(f"Ocorreu um erro no pipeline corporativo de IA: {e}")
