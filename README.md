# ProjetoParaleloVIS
Projeto de dashboard, mas agora com botão para perguntar ao AgentIA e ele gerar gráficos.

Algumas orientações: (serão melhoradas futuramente)

- Configuração do Ambiente  
python -m venv venv
- Ative o ambiente virtual  
.\venv\Scripts\activate
- Instale as dependências  
pip install -r requirements.txt
- Execute o aplicativo  
streamlit run app.py
- Variáveis de Ambiente  
Certifique-se de incluir sua API KEY no arquivo ``secrets.toml`` e dentro de uma pasta ``.streamlit`` na raiz do projeto.
Inclua no ``gitignore``.
```GEMINI_API_KEY="SUA_CHAVE_DE_API_GEMINI"
Lembre-se de incluir este arquivo/pasta no gitignore, pois essas variáveis são essenciais para o funcionamento do aplicativo, garantindo que ele possa se comunicar com a API do Gemini e ser acessível através da URL especificada.   
Certifique-se de manter essas informações seguras e não compartilhá-las publicamente.   
Após configurar as variáveis de ambiente, você pode iniciar o aplicativo usando o comando `streamlit run App.py` e acessar a interface do usuário através da URL configurada.   
