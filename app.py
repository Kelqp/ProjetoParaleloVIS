import streamlit as st
import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import geopandas as gpd
import matplotlib.pyplot as plt

# ---------------------------------------------------------
# CONFIGURAÇÕES DA PÁGINA (Estilo Acadêmico)
# ---------------------------------------------------------
st.set_page_config(
    page_title="SIH/SUS Academic Dashboard", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilização CSS adicional para um visual mais limpo e acadêmico
st.markdown("""
    <style>
    .reportview-container .main .block-container{
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    h1, h2, h3 {
        font-family: 'Times New Roman', Times, serif;
        color: #2c3e50;
    }
    .academic-caption {
        font-size: 0.85em;
        font-style: italic;
        color: #7f8c8d;
        text-align: center;
        margin-bottom: 20px;
    }
    .metric-container {
        border-left: 4px solid #34495e;
        padding-left: 10px;
    }
    </style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# CONEXÃO E CARREGAMENTO DE DADOS (CACHED)
# ---------------------------------------------------------
DB_PATH = "caminho do seu db.duckdb"  # Atualize para o caminho do seu banco de dados local

@st.cache_resource
def get_db_connection():
    return duckdb.connect(DB_PATH, read_only=True)

@st.cache_data(show_spinner="Carregando detalhes temporais do município...")
def get_mun_temp(cod_ibge):
    conn_mun = get_db_connection()
    q = f"""
        SELECT 
            ano,
            mes,
            COUNT(CASE WHEN ibge_municipio_residencia = ibge_municipio_hospital THEN 1 END) AS Residentes,
            COUNT(CASE WHEN ibge_municipio_residencia != ibge_municipio_hospital THEN 1 END) AS Migrantes
        FROM view_analitica_sih
        WHERE ibge_municipio_residencia = '{cod_ibge}'
        GROUP BY ano, mes
        ORDER BY ano, mes
    """
    return conn_mun.execute(q).fetchdf()

# ---------------------------------------------------------
# INTERFACE DE MAPEAMENTO DE COLUNAS (SIDEBAR)
# ---------------------------------------------------------
st.sidebar.header("⚙️ Configurações")
st.sidebar.markdown("Os dados já estão padronizados através de uma tabela/view analítica interna.")

@st.cache_data(show_spinner="Calculando Opções de Filtro...")
def get_filter_options():
    conn = get_db_connection()
    try:
        ano_max = int(conn.execute("SELECT MAX(EXTRACT(YEAR FROM DT_INTER)) FROM internacoes").fetchone()[0] or 2023)
        ano_min = ano_max - 4
        df_espec = conn.execute("SELECT DISTINCT DESCRICAO FROM especialidade WHERE DESCRICAO IS NOT NULL ORDER BY DESCRICAO").fetchdf()
        especialidades = df_espec['DESCRICAO'].tolist()
        
        # Tentando buscar colunas extras se disponíveis (Sexo)
        colunas_internacoes = [c[1].upper() for c in conn.execute("PRAGMA table_info(internacoes)").fetchall()]
        sexos = []
        if 'SEXO' in colunas_internacoes:
            sexos = conn.execute("SELECT DISTINCT SEXO FROM internacoes WHERE SEXO IS NOT NULL").fetchdf()['SEXO'].tolist()
            
        # Pega uf da residencia
        ufs_df = conn.execute("SELECT DISTINCT SUBSTRING(CAST(MUNIC_RES AS VARCHAR), 1, 2) AS UF FROM internacoes WHERE MUNIC_RES IS NOT NULL").fetchdf()
        ufs_codigos = sorted(ufs_df['UF'].tolist()) if not ufs_df.empty else []
        
        return ano_min, ano_max, especialidades, sexos, ufs_codigos, colunas_internacoes
    except Exception as e:
        return 2018, 2023, [], [], [], []

@st.cache_data(show_spinner="Calculando View Analítica Padronizada...")
def load_data(ano_ini, ano_fim, especialidades, estados_sel, cidades_sel, generos_sel, idade_range, has_idade, has_sexo):
    conn = get_db_connection()
    
    # Montando as colunas condicionais
    idade_col = "i.IDADE" if has_idade else "NULL"
    sexo_col = "i.SEXO" if has_sexo else "NULL"

    # Cria uma view temporária ou física unificada
    try:
        conn.execute(f"CREATE OR REPLACE TEMPORARY VIEW view_analitica_sih AS SELECT i.N_AIH, EXTRACT(YEAR FROM i.DT_INTER) AS ano, EXTRACT(MONTH FROM i.DT_INTER) AS mes, i.MUNIC_RES AS ibge_municipio_residencia, h.MUNIC_MOV AS ibge_municipio_hospital, i.ESPEC AS cod_especialidade, e.DESCRICAO AS nome_especialidade, {idade_col} AS idade, {sexo_col} AS sexo FROM internacoes i LEFT JOIN hospital h ON i.CNES = h.CNES LEFT JOIN especialidade e ON i.ESPEC = e.ESPEC")
    except Exception as e:
        raise Exception(f"Erro ao criar View Analítica: {e}")

    # Escopo Temporal
    filtro_tempo = f"ano BETWEEN {ano_ini} AND {ano_fim}"
    filtros = [filtro_tempo]
    
    if especialidades:
        espec_str = ", ".join([f"'{e.replace(chr(39), chr(39)+chr(39))}'" for e in especialidades])
        filtros.append(f"nome_especialidade IN ({espec_str})")
        
    if estados_sel:
        est_str = ", ".join([f"'{e}'" for e in estados_sel])
        filtros.append(f"SUBSTRING(CAST(ibge_municipio_residencia AS VARCHAR), 1, 2) IN ({est_str})")
        
    if cidades_sel:
        # Cidades vêm como "123456 - Nome", então quebramos e pegamos o codigo
        cid_str = ", ".join([f"'{c.split(' - ')[0]}'" for c in cidades_sel])
        filtros.append(f"CAST(ibge_municipio_residencia AS VARCHAR) IN ({cid_str})")
        
    if generos_sel and has_sexo:
        gen_str = ", ".join([f"'{g}'" for g in generos_sel])
        filtros.append(f"sexo IN ({gen_str})")
        
    if idade_range and has_idade:
        filtros.append(f"idade BETWEEN {idade_range[0]} AND {idade_range[1]}")

    where_clause = "WHERE " + " AND ".join(filtros)

    # 2. KPIs
    kpi_query = f"""
        SELECT 
            COUNT(*) AS total_internacoes,
            COUNT(CASE WHEN ibge_municipio_residencia != ibge_municipio_hospital THEN 1 END) AS total_migrantes
        FROM view_analitica_sih
        {where_clause}
    """
    df_kpi = conn.execute(kpi_query).fetchdf()

    # 3. Evolução Temporal
    temporal_query = f"""
        SELECT 
            ano || '-' || LPAD(CAST(mes AS VARCHAR), 2, '0') AS periodo,
            COUNT(CASE WHEN ibge_municipio_residencia = ibge_municipio_hospital THEN 1 END) AS Residentes,
            COUNT(CASE WHEN ibge_municipio_residencia != ibge_municipio_hospital THEN 1 END) AS Migrantes
        FROM view_analitica_sih
        {where_clause}
        GROUP BY ano, mes
        ORDER BY ano, mes
    """
    df_temporal = conn.execute(temporal_query).fetchdf()

    # 4. Taxa de Exportação
    evasao_query = f"""
        SELECT 
            ibge_municipio_residencia AS cod_ibge_origem,
            COUNT(*) as total_demandado,
            COUNT(CASE WHEN ibge_municipio_residencia != ibge_municipio_hospital THEN 1 END) AS total_evadido,
            (COUNT(CASE WHEN ibge_municipio_residencia != ibge_municipio_hospital THEN 1 END) * 100.0 / COUNT(*)) AS tx_evasao
        FROM view_analitica_sih
        {where_clause}
        GROUP BY ibge_municipio_residencia
        HAVING COUNT(*) > 50
        ORDER BY tx_evasao DESC
    """
    df_evasao = conn.execute(evasao_query).fetchdf()

    # 5. Vazios Assistenciais
    vazios_query = f"""
        WITH stats_espec AS (
            SELECT 
                ibge_municipio_residencia,
                nome_especialidade AS Especialidade,
                COUNT(*) AS total_demanda,
                COUNT(CASE WHEN ibge_municipio_residencia = ibge_municipio_hospital THEN 1 END) AS atendimentos_locais
            FROM view_analitica_sih
            {where_clause}
            GROUP BY ibge_municipio_residencia, nome_especialidade
        )
        SELECT 
            ibge_municipio_residencia AS Municipio,
            Especialidade,
            total_demanda AS Pacientes_Deslocados
        FROM stats_espec
        WHERE atendimentos_locais = 0 
          AND total_demanda >= 10
        ORDER BY total_demanda DESC
    """
    df_vazios = conn.execute(vazios_query).fetchdf()

    # 6. Polos Receptores
    polos_query = f"""
        SELECT 
            ibge_municipio_hospital AS cod_ibge_hospital,
            COUNT(*) AS total_admissoes,
            COUNT(CASE WHEN ibge_municipio_residencia != ibge_municipio_hospital THEN 1 END) AS pacientes_recebidos
        FROM view_analitica_sih
        {where_clause}
        GROUP BY ibge_municipio_hospital
        ORDER BY pacientes_recebidos DESC
    """
    df_polos = conn.execute(polos_query).fetchdf()

    return {
        "anos": (ano_ini, ano_fim),
        "kpi": df_kpi,
        "temporal": df_temporal,
        "evasao": df_evasao,
        "vazios": df_vazios,
        "polos": df_polos
    }

@st.cache_data(show_spinner="Carregando nomes de municípios (IBGE)...")
def load_municipios():
    try:
        import requests
        url = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"
        data = requests.get(url).json()
        df_mun = pd.DataFrame(data)
        df_mun['cod_ibge_6'] = df_mun['id'].astype(str).str[:6]
        return df_mun[['cod_ibge_6', 'nome']].copy()
    except:
        return pd.DataFrame(columns=['cod_ibge_6', 'nome'])

@st.cache_data(show_spinner="Carregando malha espacial do IBGE...")
def load_geodata():
    import requests
    import tempfile
    import os
    import json
    # API Oficial do IBGE para a malha municipal do Brasil
    url_ibge = "https://raw.githubusercontent.com/tbrugz/geodata-br/master/geojson/geojs-100-mun.json"
    
    try:
        response = requests.get(url_ibge, timeout=30)
        response.raise_for_status()
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False, encoding='utf-8') as f:
            f.write(response.text)
            tmp_filepath = f.name
            
        gdf = gpd.read_file(tmp_filepath)
        os.remove(tmp_filepath)
        
        # O IBGE pode retornar chaves diferentes dependendo da versão da API
        if 'codarea' in gdf.columns:
            gdf['cod_ibge_6'] = gdf['codarea'].astype(str).str[:6]
        elif 'id' in gdf.columns:
            gdf['cod_ibge_6'] = gdf['id'].astype(str).str[:6]
        elif 'CD_MUN' in gdf.columns:
            gdf['cod_ibge_6'] = gdf['CD_MUN'].astype(str).str[:6]
        else:
            # Caso não encontre a coluna, logamos as colunas e tentamos a primeira
            print(f"Colunas do IBGE não reconhecidas: {gdf.columns}")
            gdf['cod_ibge_6'] = "000000"
        return gdf
    except Exception as e:
        # Se ocorrer erro, criamos um GeoDataFrame vazio para não quebrar o dashboard
        st.error(f"Aviso ao buscar a malha (o mapa pode não renderizar): {e}")
        return gpd.GeoDataFrame()

# ---------------------------------------------------------
# ESTRUTURA DO DASHBOARD
# ---------------------------------------------------------

col_title, col_agent = st.columns([0.8, 0.2])
with col_title:
    st.title("Padrões de Migração de Pacientes no Sistema Único de Saúde (SIH/SUS)")
    st.markdown("Uma análise estrutural de evasão, polos receptores e vazios assistenciais baseada nos Registros de Internação.")

with col_agent:
    st.write("") # Margin
    st.write("") 
    if st.button("🤖 Pergunte ao AgenteIA", use_container_width=True):
        from agent import chat_agent
        chat_agent()

st.divider()

try:
    ano_min_db, ano_max_db, lista_especialidades, sexos, ufs_codigos, colunas_internacoes = get_filter_options()
    has_sexo = 'SEXO' in colunas_internacoes
    has_idade = 'IDADE' in colunas_internacoes
except Exception as e:
    st.error(f"Erro ao obter opções de filtro: {e}")
    st.stop()

# Dicionário de Mapeamento de UFs (Código do IBGE -> Sigla)
MAPA_UFS = {
    '11': 'RO', '12': 'AC', '13': 'AM', '14': 'RR', '15': 'PA', '16': 'AP', '17': 'TO',
    '21': 'MA', '22': 'PI', '23': 'CE', '24': 'RN', '25': 'PB', '26': 'PE', '27': 'AL', '28': 'SE', '29': 'BA',
    '31': 'MG', '32': 'ES', '33': 'RJ', '35': 'SP',
    '41': 'PR', '42': 'SC', '43': 'RS',
    '50': 'MS', '51': 'MT', '52': 'GO', '53': 'DF'
}

# SIDEBAR (Filtros globais e Metodologia)
with st.sidebar:
    st.header("Metodologia & Filtros")
    
    st.markdown("### 📅 Filtros Temporais")
    ano_selecionado = st.slider(
        "Selecione o período (Intervalo de Anos):", 
        min_value=ano_min_db, 
        max_value=ano_max_db, 
        value=(ano_min_db, ano_max_db)
    )
    
    st.markdown("### 📍 Filtros Geográficos")
    lista_siglas_uf = [MAPA_UFS.get(c, c) for c in ufs_codigos if c in MAPA_UFS]
    
    est_sel_nomes = st.multiselect("Estado(s) de Residência:", options=lista_siglas_uf, default=[])
    # Reverte sigla para codigo ibge
    est_selecionados = [k for k, v in MAPA_UFS.items() if v in est_sel_nomes]
    
    # Carrega nomes dos municípios para o filtro
    df_nomes_mun_sidebar = load_municipios()
    # Se estados selecionados, filtra municipios
    if est_selecionados:
        df_nomes_mun_sidebar = df_nomes_mun_sidebar[df_nomes_mun_sidebar['cod_ibge_6'].astype(str).str[:2].isin(est_selecionados)]
        
    cidades_opcoes = df_nomes_mun_sidebar['cod_ibge_6'] + " - " + df_nomes_mun_sidebar['nome']
    cid_selecionadas = st.multiselect("Cidade(s) de Residência (Máximo de 15 recomendável):", options=cidades_opcoes.tolist(), default=[], max_selections=30)
    
    st.markdown("### 👤 Filtros Demográficos")
    generos_selecionados = []
    if has_sexo and sexos:
        generos_selecionados = st.multiselect("Gênero do Paciente:", options=sexos, default=[])

    idade_selecionada = None
    if has_idade:
        idade_selecionada = st.slider(
            "Faixa Etária (Anos):",
            min_value=0, max_value=120, value=(0, 120)
        )
        
    st.markdown("### ⚕️ Filtros Clínicos")
    especialidades_selecionadas = st.multiselect(
        "Filtrar por Especialidade(s):",
        options=lista_especialidades,
        default=[],
        help="Deixe em branco para considerar todas."
    )
    
    st.divider()
    st.info(f"**Janela:** {ano_selecionado[0]} a {ano_selecionado[1]}")
    st.markdown("""
        **Glossário:**
        - **Residente:** Paciente internado na própria cidade.
        - **Migrante/Evadido:** Paciente buscando atendimento em outro município.
        - **Vazio Assistencial:** 100% de exportação da demanda (zero assistência local).
    """)
    st.caption("Dados processados localmente via DuckDB.")

try:
    dados = load_data(
        ano_ini=ano_selecionado[0], 
        ano_fim=ano_selecionado[1], 
        especialidades=especialidades_selecionadas,
        estados_sel=est_selecionados,
        cidades_sel=cid_selecionadas,
        generos_sel=generos_selecionados,
        idade_range=idade_selecionada,
        has_idade=has_idade,
        has_sexo=has_sexo
    )
except Exception as e:
    st.error(f"Erro ao processar os dados aplicando filtros: {e}")
    st.stop()

ano_min, ano_max = dados["anos"]

# ---------------------------------------------------------
# RESUMO EXECUTIVO (KPIs)
# ---------------------------------------------------------
st.header("Resumo Epidemiológico")
kpi_data = dados['kpi'].iloc[0]
total_int = kpi_data['total_internacoes']
total_mig = kpi_data['total_migrantes']
tx_global = (total_mig / total_int * 100) if total_int > 0 else 0

c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("<div class='metric-container'>", unsafe_allow_html=True)
    st.metric("Total de Internações Analisadas", f"{total_int:,.0f}".replace(',', '.'))
    st.markdown("</div>", unsafe_allow_html=True)
with c2:
    st.markdown("<div class='metric-container'>", unsafe_allow_html=True)
    st.metric("Total de Migrações (Evasões)", f"{total_mig:,.0f}".replace(',', '.'))
    st.markdown("</div>", unsafe_allow_html=True)
with c3:
    st.markdown("<div class='metric-container'>", unsafe_allow_html=True)
    st.metric("Taxa Global de Evasão", f"{tx_global:.2f}%")
    st.markdown("</div>", unsafe_allow_html=True)

st.divider()

# ---------------------------------------------------------
# PAINÉIS (TABS)
# ---------------------------------------------------------
t1, t2, t3, t4 = st.tabs([
    "📈 Dinâmica Temporal", 
    "🚑 Polos & Evasões (Municípios)", 
    "⚠️ Vazios Assistenciais (Especialidades)",
    "🗺️ Mapeamento Geoespacial"
])

# ---- TAB 1: TEMPORAL ----
with t1:
    st.subheader("Figure 1: Evolução Longitudinal de Internações Locais e Externas")
    
    df_temp = dados['temporal']
    if not df_temp.empty:
        # Plotly Line Chart acadêmico
        fig_temp = px.line(
            df_temp, 
            x='periodo', 
            y=['Residentes', 'Migrantes'],
            markers=True,
            color_discrete_map={"Residentes": "#2c3e50", "Migrantes": "#e74c3c"},
            labels={"periodo": "Mês-Ano de Competência", "value": "Volume de Pacientes", "variable": "Tipo de Paciente"}
        )
        fig_temp.update_layout(
            plot_bgcolor='white',
            legend=dict(title=None, orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(t=50, l=50, r=20, b=50),
            hovermode="x unified"
        )
        fig_temp.update_yaxes(showgrid=True, gridwidth=1, gridcolor='LightGray')
        fig_temp.update_xaxes(showgrid=False)
        st.plotly_chart(fig_temp, use_container_width=True)
        st.markdown("<p class='academic-caption'>Figure 1: Série temporal descrevendo o volume mensal de retenção local versus evasão assistencial (Fluxo de pacientes para fora de sua residência original).</p>", unsafe_allow_html=True)
    else:
        st.info("Dados temporais não disponíveis.")

# ---- TAB 2: POLOS & EVASÕES ----
with t2:
    col_a, col_b = st.columns(2)
    df_nomes_mun = load_municipios()
    
    with col_a:
        st.subheader("Table 1: High-Dependency Nodes (Top 20)")
        st.write("Municípios com **maiores taxas de exportação** de pacientes (Base > 50 internações).")
        df_ev_display = dados['evasao'].head(20).copy()
        
        # Junta com os nomes dos municípios
        df_ev_display['cod_ibge_origem_str'] = df_ev_display['cod_ibge_origem'].astype(str)
        df_ev_display = df_ev_display.merge(df_nomes_mun, left_on='cod_ibge_origem_str', right_on='cod_ibge_6', how='left')
        df_ev_display['Municipio'] = df_ev_display['cod_ibge_origem_str'] + " - " + df_ev_display['nome'].fillna("Desconhecido")
        
        df_ev_display['tx_evasao'] = df_ev_display['tx_evasao'].apply(lambda x: f"{x:.2f}%")
        df_ev_display = df_ev_display[['Municipio', 'total_demandado', 'total_evadido', 'tx_evasao']]
        
        df_ev_display.rename(columns={
            'Municipio': 'Mun. Origem',
            'total_demandado': 'Demanda Total',
            'total_evadido': 'Volume Exportado',
            'tx_evasao': 'Taxa de Evasão'
        }, inplace=True)
        st.dataframe(df_ev_display, use_container_width=True, hide_index=True)
        st.markdown("<p class='academic-caption'>Table 1: Demonstra o déficit assistencial dos municípios com alta dependência externa.</p>", unsafe_allow_html=True)
        
    with col_b:
        st.subheader("Table 2: Polos de Atração (Top 20)")
        st.write("Estabelecimentos/Cidades que **mais absorvem demandas externas**.")
        df_po_display = dados['polos'].head(20).copy()
        
        # Junta com os nomes dos municípios
        df_po_display['cod_ibge_hospital_str'] = df_po_display['cod_ibge_hospital'].astype(str)
        df_po_display = df_po_display.merge(df_nomes_mun, left_on='cod_ibge_hospital_str', right_on='cod_ibge_6', how='left')
        df_po_display['Municipio'] = df_po_display['cod_ibge_hospital_str'] + " - " + df_po_display['nome'].fillna("Desconhecido")
        
        df_po_display = df_po_display[['Municipio', 'total_admissoes', 'pacientes_recebidos']]
        
        df_po_display.rename(columns={
            'Municipio': 'Mun. Destino',
            'total_admissoes': 'Admissões Totais',
            'pacientes_recebidos': 'Pacientes de Fora'
        }, inplace=True)
        st.dataframe(df_po_display, use_container_width=True, hide_index=True)
        st.markdown("<p class='academic-caption'>Table 2: Identifica os principais centros de referência (hubs) receptores na rede.</p>", unsafe_allow_html=True)
        
    st.divider()
    st.subheader("Exportação de Dados")
    st.write("Exporte as tabelas compiladas para análises aprofundadas em formato de planilha ou delimitado por vírgulas. *(Para salvar em PDF, pressione `Ctrl+P` e escolha 'Salvar como PDF')*")
    
    col_exp1, col_exp2 = st.columns(2)
    import io
    
    # Prepara um arquivo Excel em memória contendo as tabelas
    def to_excel(df_evasao, df_polos):
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_evasao.to_excel(writer, index=False, sheet_name='Evasoes')
            df_polos.to_excel(writer, index=False, sheet_name='Polos Receptores')
        return output.getvalue()
    
    # Converte para CSV
    csv_evasao = df_ev_display.to_csv(index=False).encode('utf-8')
    csv_polos = df_po_display.to_csv(index=False).encode('utf-8')
    
    with col_exp1:
        st.download_button(
            label="📥 Baixar Taxas de Evasão (CSV)",
            data=csv_evasao,
            file_name=f'evasoes_{ano_selecionado[0]}_{ano_selecionado[1]}.csv',
            mime='text/csv',
        )
        try:
            excel_data = to_excel(df_ev_display, df_po_display)
            st.download_button(
                label="📊 Baixar Ambas as Tabelas (XLSX)",
                data=excel_data,
                file_name=f'relatorio_sih_{ano_selecionado[0]}_{ano_selecionado[1]}.xlsx',
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
        except Exception as exc:
            st.warning("⚠️ Instale `xlsxwriter` para habilitar download em formato XLSX (`pip install xlsxwriter`).")

# ---- TAB 3: VAZIOS ASSISTENCIAIS ----
with t3:
    st.subheader("Figure 2 & Table 3: Vazios Assistenciais Críticos Absolutos")
    st.write("Identificação de Municípios e Especialidades cuja taxa de **atendimento local foi de 0%** (i.e., todos os pacientes necessitaram viajar para realizar o procedimento). Exibindo apenas demandas ≥ 10 pacientes.")
    
    df_vazios = dados['vazios'].copy()
    
    if not df_vazios.empty:
        # Pega os nomes para os municípios também
        if 'df_nomes_mun' not in locals():
            df_nomes_mun = load_municipios()
            
        df_vazios['cod_ibge_str'] = df_vazios['Municipio'].astype(str)
        df_vazios = df_vazios.merge(df_nomes_mun, left_on='cod_ibge_str', right_on='cod_ibge_6', how='left')
        df_vazios['Municipio_Nome'] = df_vazios['cod_ibge_str'] + " - " + df_vazios['nome'].fillna("Desconhecido")
        df_vazios = df_vazios[['Municipio_Nome', 'Especialidade', 'Pacientes_Deslocados']]
        df_vazios.rename(columns={'Municipio_Nome': 'Municipio'}, inplace=True)
        
        c_fig, c_tab = st.columns([1.5, 1])
        
        with c_fig:
            # Agregando para mostrar as especialidades com mais "vazios absolutos"
            df_vaz_grouped = df_vazios.groupby('Especialidade')['Pacientes_Deslocados'].sum().reset_index()
            df_vaz_grouped = df_vaz_grouped.sort_values(by='Pacientes_Deslocados', ascending=True).tail(10)
            
            fig_vaz = px.bar(
                df_vaz_grouped, 
                x='Pacientes_Deslocados', 
                y='Especialidade', 
                orientation='h',
                color_discrete_sequence=['#34495e'],
                labels={'Pacientes_Deslocados': 'Total de Pacientes Deslocados'}
            )
            fig_vaz.update_layout(plot_bgcolor='white', margin=dict(l=0, r=0, t=10, b=0))
            fig_vaz.update_xaxes(showgrid=True, gridcolor='LightGray')
            st.plotly_chart(fig_vaz, use_container_width=True)
            st.markdown("<p class='academic-caption'>Figure 2: Especialidades com maior severidade de vazios assistenciais (Demanda reprimida localmente exportada na totalidade).</p>", unsafe_allow_html=True)
            
        with c_tab:
            st.dataframe(
                df_vazios.rename(columns={"Pacientes_Deslocados": "Volume Deslocado"}), 
                use_container_width=True, 
                hide_index=True
            )
            st.markdown("<p class='academic-caption'>Table 3: Municípios mapeados com 100% de exportação da amostra detalhada acima.</p>", unsafe_allow_html=True)
    else:
        st.success("Nenhum município apresentou 100% de vazão para demandas > 10 pacientes nos últimos 5 anos analisados.")

# ---- TAB 4: MAPEAMENTO GEOESPACIAL ----
with t4:
    st.subheader("Figure 3: Distribuição Espacial das Taxas de Evasão")
    st.write("Mapeamento coroplético processado via **GeoPandas**, cruzando as chaves de 6 dígitos com a base territorial oficial do IBGE.")
    
    try:
        gdf = load_geodata()
        
        if gdf.empty:
            st.warning("Não foi possível renderizar o mapa interativo: A malha geográfica não foi carregada. Verifique se há acesso à internet para acessar a malha do IBGE.")
        else:
            # Pareamento (JOIN) entre a malha IBGE e nosso Dataframe DuckDB (MUNIC_RES)
            df_ev_map = dados['evasao'].copy()
            # Garante que os tipos sejam string para o cruzamento correto
            df_ev_map['cod_ibge_origem'] = df_ev_map['cod_ibge_origem'].astype(str)
            merged_gdf = gdf.merge(df_ev_map, left_on='cod_ibge_6', right_on='cod_ibge_origem', how='inner')
            
            # Preenchendo buracos geográficos para que o mapa saia completo visualmente
            merged_gdf['tx_evasao'] = merged_gdf['tx_evasao'].fillna(0)
            
            if merged_gdf.empty:
                st.warning("A malha geográfica foi carregada, mas não houve correspondência com os códigos IBGE dos municípios da base de dados. O mapa não pôde ser gerado.")
            else:
                # Obter nomes para tooltip
                if 'df_nomes_mun' not in locals():
                    df_nomes_mun = load_municipios()
                merged_gdf['cod_ibge_origem_str'] = merged_gdf['cod_ibge_origem'].astype(str)
                
                # Previne KeyError separando o nome corretamente
                df_nomes_mun_renamed = df_nomes_mun.rename(columns={'nome': 'nome_mun', 'cod_ibge_6': 'cod_ibge_6_mun'})
                merged_gdf = merged_gdf.merge(df_nomes_mun_renamed, left_on='cod_ibge_origem_str', right_on='cod_ibge_6_mun', how='left')
                
                if 'name' in merged_gdf.columns:
                    merged_gdf['Municipio'] = merged_gdf['nome_mun'].fillna(merged_gdf['name']).fillna('Desconhecido')
                else:
                    merged_gdf['Municipio'] = merged_gdf['nome_mun'].fillna('Desconhecido')
                
                # Para maior compatibilidade com plotly e GeoPandas modernos:
                merged_gdf = merged_gdf.to_crs(epsg=4326)
                
                # Renderização interativa via Plotly Express
                fig_map = px.choropleth_mapbox(
                    merged_gdf,
                    geojson=merged_gdf.geometry,
                    locations=merged_gdf.index,
                    color='tx_evasao',
                    color_continuous_scale="OrRd",
                    mapbox_style="carto-positron",
                    zoom=3.5,
                    center={"lat": -14.235, "lon": -51.925},
                    opacity=0.8,
                    hover_name="Municipio",
                    custom_data=['cod_ibge_origem', 'Municipio'],
                    hover_data={
                        "tx_evasao": ":.2f",
                        "total_demandado": True,
                        "total_evadido": True
                    },
                    labels={
                        "tx_evasao": "Taxa de Evasão (%)",
                        "total_demandado": "Demanda Total",
                        "total_evadido": "Pacientes Evadidos"
                    }
                )
                
                fig_map.update_layout(
                    margin={"r":0,"t":0,"l":0,"b":0},
                    coloraxis_colorbar=dict(
                        title="Taxa de Evasão (%)",
                        orientation="h",
                        yanchor="bottom",
                        y=0.02,
                        xanchor="center",
                        x=0.5,
                        len=0.5
                    )
                )
                
                # Habilita eventos de clique se a versão do Streamlit suportar
                try:
                    event = st.plotly_chart(fig_map, use_container_width=True, on_select="rerun")
                    sel_cod_ibge = None
                    sel_municipio = None
                    
                    if event and "selection" in event and event["selection"]["points"]:
                        point = event["selection"]["points"][0]
                        if "customdata" in point:
                            sel_cod_ibge = point["customdata"][0]
                            sel_municipio = point["customdata"][1]
                            
                    if sel_cod_ibge:
                        st.markdown(f"### Detalhamento: **{sel_municipio}**")
                        
                        # Mostra a evolução ao longo do ano para a cidade clicada
                        df_temporal_mun = dados['temporal']
                        
                        # Filtra temporal para o município
                        df_mun_temp = get_mun_temp(sel_cod_ibge)
                        
                        if not df_mun_temp.empty:
                            df_mun_temp['data'] = pd.to_datetime(df_mun_temp['ano'].astype(str) + '-' + df_mun_temp['mes'].astype(str).str.zfill(2) + '-01')
                            
                            fig_mun = px.line(
                                df_mun_temp, x='data', y=['Residentes', 'Migrantes'], 
                                title=f"Internações na Origem vs. Evasão: {sel_municipio}",
                                markers=True
                            )
                            fig_mun.update_layout(plot_bgcolor='white', xaxis_title="Período", yaxis_title="Nº de Casos")
                            fig_mun.update_xaxes(showgrid=True, gridcolor='LightGray')
                            fig_mun.update_yaxes(showgrid=True, gridcolor='LightGray')
                            st.plotly_chart(fig_mun, use_container_width=True)
                        else:
                            st.info("Não há dados mensais suficientes para detalhar este município.")
                        
                except TypeError:
                    # Fallback para versões antigas do Streamlit que não suportam on_select
                    st.plotly_chart(fig_map, use_container_width=True)
                
                st.markdown("<p class='academic-caption'>Figure 3: Mapa interativo das taxas coropléticas de migração. Áreas com cores mais intensas apontam vulnerabilidade aguda no atendimento local. <b>Clique em um município para ver seu perfil no gráfico abaixo (em navegadores compatíveis).</b></p>", unsafe_allow_html=True)

            
    except Exception as e:
        st.error(f"Erro ao compilar o geoprocessamento: {e}")
        st.info("💡 Para seu diretório local, garanta que as dependências existem: `pip install geopandas matplotlib`")

st.divider()
