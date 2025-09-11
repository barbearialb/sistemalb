import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore, auth
from google.cloud.firestore_v1.field_path import FieldPath
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
import json
import google.api_core.exceptions
import google.api_core.retry as retry
import random
import pandas as pd
import time

# --- NOVO BLOCO DE ESTILO (CSS) ---
st.markdown("""
<style>
    /* Estilo para os botões de agendamento */
    .stButton > button {
        width: 100%;
        border-radius: 5px;
        background-color: #28a745;
        color: white;
        border: none;
    }
    .stButton > button:hover {
        background-color: #218838;
        color: white;
        border: none;
    }
    /* Estilo para as caixas de status (Ocupado, Almoço, etc.) */
    .status-box {
        padding: 8px;
        margin: 2px 0;
        text-align: center;
        border-radius: 5px;
        font-weight: bold;
    }
    /* Estilo para a coluna do horário, para alinhar ao centro */
    .time-slot {
        display: flex;
        align-items: center;
        justify-content: center;
        height: 100%;
        font-weight: bold;
        padding-top: 8px;
    }
</style>
""", unsafe_allow_html=True)

# Carregar as credenciais do Firebase e-mail a partir do Streamlit secrets
FIREBASE_CREDENTIALS = None
EMAIL = None
SENHA = None

try:
    # Carregar credenciais do Firebase
    firebase_credentials_json = st.secrets["firebase"]["FIREBASE_CREDENTIALS"]
    FIREBASE_CREDENTIALS = json.loads(firebase_credentials_json)

    # Carregar credenciais de e-mail
    EMAIL = st.secrets["email"]["EMAIL_CREDENCIADO"]
    SENHA = st.secrets["email"]["EMAIL_SENHA"]

except KeyError as e:
    st.error(f"Chave ausente no arquivo secrets.toml: {e}")
except json.JSONDecodeError as e:
    st.error(f"Erro ao decodificar as credenciais do Firebase: {e}")
except Exception as e:
    st.error(f"Erro inesperado: {e}")

# Inicializar Firebase com as credenciais
if FIREBASE_CREDENTIALS:
    if not firebase_admin._apps:  # Verifica se o Firebase já foi inicializado
        try:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            st.error(f"Erro ao inicializar o Firebase: {e}")


# Obter referência do Firestore
db = firestore.client() if firebase_admin._apps else None

# Dados básicos
# A lista de horários base será gerada dinamicamente na tabela

servicos = {
    "Tradicional",
    "Social",
    "Degradê",
    "Pezim",
    "Navalhado",
    "Barba",
    "Abordagem de visagismo",
    "Consultoria de visagismo",
}

# Lista de serviços para exibição
lista_servicos = servicos

barbeiros = ["Aluizio", "Lucas Borges"]

# Função para enviar e-mail
def enviar_email(assunto, mensagem):
    # Proteção extra para caso as credenciais não carreguem
    if not EMAIL or not SENHA:
        st.warning("Credenciais de e-mail não configuradas. E-mail não enviado.")
        return
    try:
        msg = MIMEText(mensagem)
        msg['Subject'] = assunto
        msg['From'] = EMAIL
        msg['To'] = EMAIL

        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL, SENHA)  # Login usando as credenciais do e-mail
            server.sendmail(EMAIL, EMAIL, msg.as_string())
    except Exception as e:
        st.error(f"Erro ao enviar e-mail: {e}")

# SUBSTITUA A FUNÇÃO INTEIRA
def salvar_agendamento(data_str, horario, nome, telefone, servicos, barbeiro):
    if not db:
        st.error("Firestore não inicializado.")
        return False

    try:
        # Converte a data string (que vem do formulário) para um objeto datetime
        data_obj = datetime.strptime(data_str, '%d/%m/%Y')
        
        # Cria o ID do documento no formato correto YYYY-MM-DD
        data_para_id = data_obj.strftime('%Y-%m-%d')
        chave_agendamento = f"{data_para_id}_{horario}_{barbeiro}"
        agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
        
        # Esta é a parte que você perguntou, agora dentro da função principal
        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            if doc.exists:
                # Se o documento já existe, a transação falha para evitar agendamento duplo
                raise ValueError("Horário já ocupado por outra pessoa.")
            
            # Se o horário estiver livre, a transação define os novos dados
            transaction.set(doc_ref, {
                'data': data_obj,
                'horario': horario,
                'nome': nome,
                'telefone': telefone,
                'servicos': servicos,
                'barbeiro': barbeiro,
                'timestamp': firestore.SERVER_TIMESTAMP
            })
        
        # Executa a transação
        transaction = db.transaction()
        update_in_transaction(transaction, agendamento_ref)
        return True # Retorna sucesso

    except ValueError as e:
        # Captura o erro "Horário já ocupado" e exibe ao utilizador
        st.error(f"Erro ao agendar: {e}")
        return False
    except Exception as e:
        st.error(f"Erro inesperado ao salvar o agendamento: {e}")
        return False

# Função para cancelar agendamento no Firestore
def cancelar_agendamento(doc_id, telefone_cliente):
    """
    Cancela um agendamento no Firestore de forma segura.
    """
    if not db:
        st.error("Firestore não inicializado.")
        return None
    
    try:
        doc_ref = db.collection('agendamentos').document(doc_id)
        doc = doc_ref.get()

        # PASSO CHAVE: VERIFICA SE O DOCUMENTO EXISTE ANTES DE TUDO
        if not doc.exists:
            st.error(f"Nenhum agendamento encontrado com o ID: {doc_id}")
            return "not_found" # Retorna um código de erro

        agendamento_data = doc.to_dict()
        telefone_no_banco = agendamento_data.get('telefone', '') # Pega o telefone de forma segura

        # Compara os telefones
        if telefone_no_banco.replace(" ", "").replace("-", "") != telefone_cliente.replace(" ", "").replace("-", ""):
            st.error("O número de telefone não corresponde ao agendamento.")
            return "phone_mismatch" # Retorna outro código de erro

        # Se tudo deu certo, deleta e retorna os dados
        doc_ref.delete()
        return agendamento_data

    except Exception as e:
        st.error(f"Ocorreu um erro ao tentar cancelar: {e}")
        return None

# no seu arquivo si (9).py

def desbloquear_horario(data_para_id, horario, barbeiro):
    """
    Desbloqueia um horário usando a data já no formato correto (YYYY-MM-DD).
    """
    if not db:
        st.error("Firestore não inicializado. Não é possível desbloquear.")
        return

    # A função agora recebe a data JÁ no formato YYY-MM-DD, então não precisa converter.
    # As linhas que causavam o erro foram removidas.
    
    chave_bloqueio = f"{data_para_id}_{horario}_{barbeiro}_BLOQUEADO"
    agendamento_ref = db.collection('agendamentos').document(chave_bloqueio)
    
    try:
        # Tenta apagar o documento de bloqueio diretamente.
        # Se o documento não existir, o Firestore não faz nada e não gera erro.
        agendamento_ref.delete()
        # A mensagem de sucesso agora é mostrada na tela principal.

    except Exception as e:
        st.error(f"Erro ao tentar desbloquear o horário seguinte: {e}")

# SUBSTITUA A FUNÇÃO INTEIRA PELA VERSÃO ABAIXO:
def buscar_agendamentos_e_bloqueios_do_dia(data_obj):
    """
    Busca todos os agendamentos do dia usando um prefixo de ID seguro (YYYY-MM-DD).
    """
    if not db:
        st.error("Firestore não inicializado.")
        return set()

    ocupados = set()
    prefixo_id = data_obj.strftime('%Y-%m-%d')

    try:
        # --- SOLUÇÃO DEFINITIVA USANDO order_by, start_at e end_at ---
        docs = db.collection('agendamentos') \
                 .order_by(FieldPath.document_id()) \
                 .start_at([prefixo_id]) \
                 .end_at([prefixo_id + '\uf8ff']) \
                 .stream()
        # --- FIM DA CORREÇÃO ---

        for doc in docs:
            ocupados.add(doc.id)

    except Exception as e:
        st.error(f"Erro ao buscar agendamentos do dia: {e}")

    return ocupados

# A SUA FUNÇÃO, COM A CORREÇÃO DO NOME DA VARIÁVEL
def verificar_disponibilidade_horario_seguinte(data, horario, barbeiro):
    if not db:
        st.error("Firestore não inicializado.")
        return False

    try:
        horario_dt = datetime.strptime(horario, '%H:%M')
        horario_seguinte_dt = horario_dt + timedelta(minutes=30)
        if horario_seguinte_dt.hour >= 20:
            return False 

        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
        data_obj = datetime.strptime(data, '%d/%m/%Y')
        data_para_id = data_obj.strftime('%Y-%m-%d')

        # --- A CORREÇÃO ESTÁ AQUI ---
        # O nome da variável foi padronizado para "chave_agendamento_seguinte"
        chave_agendamento_seguinte = f"{data_para_id}_{horario_seguinte_str}_{barbeiro}"
        agendamento_ref_seguinte = db.collection('agendamentos').document(chave_agendamento_seguinte)
        # --- FIM DA CORREÇÃO ---

        chave_bloqueio_seguinte = f"{data_para_id}_{horario_seguinte_str}_{barbeiro}_BLOQUEADO"
        bloqueio_ref_seguinte = db.collection('agendamentos').document(chave_bloqueio_seguinte)

        doc_agendamento_seguinte = agendamento_ref_seguinte.get()
        doc_bloqueio_seguinte = bloqueio_ref_seguinte.get()

        return not doc_agendamento_seguinte.exists and not doc_bloqueio_seguinte.exists

    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore ao verificar horário seguinte: {e}")
        return False
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade do horário seguinte: {e}")
        return False
        
# Função para bloquear horário para um barbeiro específico
def bloquear_horario(data, horario, barbeiro):
    if not db:
        st.error("Firestore não inicializado. Não é possível bloquear.")
        return False

    # 1. Converte a string de data "dd/mm/yyyy" para um objeto de data.
    try:
        data_obj = datetime.strptime(data, '%d/%m/%Y')
    except ValueError:
        st.error("Formato de data inválido para bloqueio.")
        return False

    # 2. Usa o objeto de data para criar o ID no formato CORRETO (YYYY-MM-DD).
    data_para_id = data_obj.strftime('%Y-%m-%d')
    chave_bloqueio = f"{data_para_id}_{horario}_{barbeiro}_BLOQUEADO"

    try:
        # 3. Usa a chave correta para criar o documento de bloqueio.
        db.collection('agendamentos').document(chave_bloqueio).set({
            'nome': "BLOQUEADO",
            'telefone': "BLOQUEADO",
            'servicos': ["BLOQUEADO"],
            'barbeiro': barbeiro,
            'data': data_obj,  # Salva o objeto de data no documento
            'horario': horario,
            'agendado_por': 'bloqueio_interno' # Campo para identificar a origem
        })
        return True
    except Exception as e:
        st.error(f"Erro ao bloquear horário: {e}")
        return False
# --- NOVA FUNÇÃO DE LÓGICA DE STATUS ---
def determinar_status_horario(horario, barbeiro, data_obj, agendamentos_do_dia):
    """
    Determina o status de um horário específico para um barbeiro.
    Retorna uma tupla com (status, cor_de_fundo, cor_do_texto).
    """
    data_para_id = data_obj.strftime('%Y-%m-%d')
    dia_da_semana = data_obj.weekday()
    dia_do_mes = data_obj.day
    mes_do_ano = data_obj.month
    hora_int = int(horario.split(':')[0])

    intervalo_especial = mes_do_ano == 7 and 10 <= dia_do_mes <= 19

    # Regra especial: 8:00 para Lucas Borges
    if dia_da_semana < 5 and not intervalo_especial and horario == "08:00" and barbeiro == "Lucas Borges":
        return "Indisponível", "#808080", "white"

    # Regra SDJ
    if horario in ["07:00", "07:30"] and not intervalo_especial:
        return "SDJ", "#696969", "white"

    chave_agendamento = f"{data_para_id}_{horario}_{barbeiro}"
    chave_bloqueio = f"{chave_agendamento}_BLOQUEADO"
    disponivel = (chave_agendamento not in agendamentos_do_dia) and \
                 (chave_bloqueio not in agendamentos_do_dia)

    if dia_da_semana < 5:
        em_almoco = not intervalo_especial and (hora_int == 12 or hora_int == 13)
        if em_almoco:
            return "Almoço", "orange", "black"
        if disponivel:
            return "Disponível", "forestgreen", "white"
        else:
            return "Ocupado", "firebrick", "white"
    elif dia_da_semana == 5:
        if disponivel:
            return "Disponível", "forestgreen", "white"
        else:
            return "Ocupado", "firebrick", "white"
    elif dia_da_semana == 6:
        if intervalo_especial:
            if disponivel:
                return "Disponível", "forestgreen", "white"
            else:
                return "Ocupado", "firebrick", "white"
        else:
            return "Fechado", "#A9A9A9", "black"
            
    return "Indisponível", "grey", "white"

# Interface Streamlit
st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/sistemalb/blob/main/icone.png?raw=true", use_container_width=True)

# Gerenciamento da Data Selecionada no Session State
if 'data_agendamento' not in st.session_state:
    st.session_state.data_agendamento = datetime.today().date()  # Inicializar como objeto date

if 'date_changed' not in st.session_state:
    st.session_state['date_changed'] = False

def handle_date_change():
    st.session_state.data_agendamento = st.session_state.data_input_widget  # Atualizar com o objeto date
    # verificar_disponibilidade.clear() # Limpar cache se estivesse usando @st.cache_data
    st.session_state['date_changed'] = True # Indica que a data mudou
    # st.rerun() # Força o rerender da página para atualizar a tabela imediatamente (opcional, mas melhora UX)

data_agendamento_obj = st.date_input(
    "Data para visualizar disponibilidade",
    value=st.session_state.data_agendamento, # Usa o valor do session state
    min_value=datetime.today().date(), # Garante que seja um objeto date
    key="data_input_widget",
    on_change=handle_date_change
)

# Atualiza o session state se o valor do widget for diferente (necessário se não usar on_change ou rerun)
if data_agendamento_obj != st.session_state.data_agendamento:
     st.session_state.data_agendamento = data_agendamento_obj
    

# Sempre usa a data do session_state para consistência
# --- Tabela de Disponibilidade ---

# SUAS LINHAS - MANTIDAS EXATAMENTE COMO PEDIU
data_para_tabela = st.session_state.data_agendamento.strftime('%d/%m/%Y')
data_obj_tabela = st.session_state.data_agendamento

# --- NOVO: INTERFACE INTERATIVA COM ABAS E FORMULÁRIO INTELIGENTE ---

st.subheader("Disponibilidade dos Barbeiros")
st.write("Selecione um barbeiro para ver os horários disponíveis.")

data_obj_tabela = st.session_state.data_agendamento
agendamentos_do_dia = buscar_agendamentos_e_bloqueios_do_dia(data_obj_tabela)

if 'horario_selecionado' not in st.session_state:
    st.session_state.horario_selecionado = None
if 'barbeiro_selecionado_tabela' not in st.session_state:
    st.session_state.barbeiro_selecionado_tabela = None

tab_lucas, tab_aluizio = st.tabs(["Lucas Borges", "Aluizio"])
abas_barbeiros = {"Lucas Borges": tab_lucas, "Aluizio": tab_aluizio}
horarios_tabela = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

for barbeiro, tab in abas_barbeiros.items():
    with tab:
        st.markdown(f"##### Horários para **{barbeiro}**")
        for horario in horarios_tabela:
            row_cols = st.columns((1, 3))
            with row_cols[0]:
                st.markdown(f"<div class='time-slot'>{horario}</div>", unsafe_allow_html=True)
            with row_cols[1]:
                status, bg_color, text_color = determinar_status_horario(horario, barbeiro, data_obj_tabela, agendamentos_do_dia)
                if status == "Disponível":
                    if st.button("Agendar", key=f"btn_{horario}_{barbeiro}"):
                        st.session_state.horario_selecionado = horario
                        st.session_state.barbeiro_selecionado_tabela = barbeiro
                        st.rerun()
                else:
                    st.markdown(f'<div class="status-box" style="background-color:{bg_color}; color:{text_color};">{status}</div>', unsafe_allow_html=True)

st.markdown("---")

# Formulário de Agendamento Otimizado (só aparece quando um horário é selecionado)
if st.session_state.horario_selecionado and st.session_state.barbeiro_selecionado_tabela:
    st.subheader("Finalize seu Agendamento")
    st.info(f"Você está agendando para o dia **{st.session_state.data_agendamento.strftime('%d/%m/%Y')}** "
            f"às **{st.session_state.horario_selecionado}** "
            f"com **{st.session_state.barbeiro_selecionado_tabela}**.")
    with st.form("agendar_form_rapido"):
        nome = st.text_input("Seu Nome Completo")
        telefone = st.text_input("Seu Telefone (com DDD)")
        
        barbeiro_agendado = st.session_state.barbeiro_selecionado_tabela
        servicos_visagismo = ["Abordagem de visagismo", "Consultoria de visagismo"]
        
        if barbeiro_agendado == "Aluizio":
            servicos_disponiveis_form = [s for s in lista_servicos if s not in servicos_visagismo]
            st.warning("Aluizio não realiza serviços de visagismo.")
        else:
            servicos_disponiveis_form = list(lista_servicos)

        servicos_selecionados = st.multiselect("Selecione os Serviços", servicos_disponiveis_form)
        submitted = st.form_submit_button("Confirmar Agendamento")

    if submitted:
        # ... (Toda a sua lógica de validação e salvamento continua aqui)
        # Este bloco é o mesmo que já discutimos, validando nome, telefone, corte+barba e salvando.
        if not nome or not telefone or not servicos_selecionados:
            st.error("Por favor, preencha seu nome, telefone e selecione pelo menos um serviço.")
            st.stop()
        
        data_agendamento_str_form = st.session_state.data_agendamento.strftime('%d/%m/%Y')
        horario_agendamento = st.session_state.horario_selecionado
        
        precisa_bloquear_proximo = False
        corte_selecionado = any(corte in servicos_selecionados for corte in ["Tradicional", "Social", "Degradê", "Navalhado"])
        barba_selecionada = "Barba" in servicos_selecionados

        if corte_selecionado and barba_selecionada:
            if not verificar_disponibilidade_horario_seguinte(data_agendamento_str_form, horario_agendamento, barbeiro_agendado):
                horario_seguinte_dt = datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)
                horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
                st.error(f"O barbeiro {barbeiro_agendado} não poderá atender para corte e barba, pois já está ocupado no horário seguinte ({horario_seguinte_str}). Por favor, escolha serviços que caibam em 30 minutos ou selecione outro horário.")
                st.stop()
            else:
                precisa_bloquear_proximo = True
        
        with st.spinner("Processando agendamento..."):
            agendamento_salvo = salvar_agendamento(data_agendamento_str_form, horario_agendamento, nome, telefone, list(servicos_selecionados), barbeiro_agendado)
            if agendamento_salvo:
                if precisa_bloquear_proximo:
                    horario_seguinte_dt = datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)
                    horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
                    bloquear_horario(data_agendamento_str_form, horario_seguinte_str, barbeiro_agendado)

                resumo = f"Nome: {nome}\nTelefone: {telefone}\nData: {data_agendamento_str_form}\nHorário: {horario_agendamento}\nBarbeiro: {barbeiro_agendado}\nServiços: {', '.join(servicos_selecionados)}"
                enviar_email("Agendamento Confirmado", resumo)
                
                imagem_bytes = gerar_imagem_resumo(nome, data_agendamento_str_form, horario_agendamento, barbeiro_agendado, servicos_selecionados)
                
                st.success("Agendamento confirmado!")
                
                if imagem_bytes:
                    st.download_button(
                        label="📥 Baixar Resumo do Agendamento",
                        data=imagem_bytes,
                        file_name=f"agendamento_{nome.split(' ')[0]}.png",
                        mime="image/png"
                    )
                st.session_state.horario_selecionado = None
                st.session_state.barbeiro_selecionado_tabela = None
                time.sleep(10)
                st.rerun()
            else:
                st.error("Não foi possível completar o agendamento. O horário pode ter sido ocupado enquanto você preenchia. Por favor, atualize a página e tente novamente.")
# --- NOVO: Botão para voltar à seleção de horários ---
if st.session_state.horario_selecionado:
    if st.button("Escolher outro horário"):
        st.session_state.horario_selecionado = None
        st.session_state.barbeiro_selecionado_tabela = None
        st.rerun()    
        
# Aba de Cancelamento
with st.form("cancelar_form"):
    st.subheader("Cancelar Agendamento")
    telefone_cancelar = st.text_input("Telefone usado no Agendamento")
    data_cancelar = st.date_input("Data do Agendamento", min_value=datetime.today().date()) # Usar date()

    # Geração da lista de horários completa para cancelamento
    horarios_base_cancelamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

    horario_cancelar = st.selectbox("Horário do Agendamento", horarios_base_cancelamento) # Usa a lista completa

    barbeiro_cancelar = st.selectbox("Barbeiro do Agendamento", barbeiros)
    submitted_cancelar = st.form_submit_button("Cancelar Agendamento")

    if submitted_cancelar:
        if not telefone_cancelar:
            st.error("Por favor, informe o telefone utilizado no agendamento.")
        else:
            with st.spinner("Processando cancelamento..."):
                data_para_id = data_cancelar.strftime('%Y-%m-%d')
                doc_id_cancelar = f"{data_para_id}_{horario_cancelar}_{barbeiro_cancelar}"

                resultado_cancelamento = cancelar_agendamento(doc_id_cancelar, telefone_cancelar)

                if isinstance(resultado_cancelamento, dict):
                    agendamento_cancelado_data = resultado_cancelamento
                    servicos_cancelados = agendamento_cancelado_data.get('servicos', [])
                    corte_no_cancelado = any(corte in servicos_cancelados for corte in ["Tradicional", "Social", "Degradê", "Navalhado"])
                    barba_no_cancelado = "Barba" in servicos_cancelados
                    horario_seguinte_desbloqueado = False

                    if corte_no_cancelado and barba_no_cancelado:
                        horario_agendamento_original = agendamento_cancelado_data['horario']
                        barbeiro_original = agendamento_cancelado_data['barbeiro']
                        data_obj_original = agendamento_cancelado_data['data']

                        horario_seguinte_dt = (datetime.strptime(horario_agendamento_original, '%H:%M') + timedelta(minutes=30))
                        if horario_seguinte_dt.hour < 20:
                            horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
                            data_para_id_desbloqueio = data_obj_original.strftime('%Y-%m-%d')
                            desbloquear_horario(data_para_id_desbloqueio, horario_seguinte_str, barbeiro_original)
                            horario_seguinte_desbloqueado = True

        # --- A sua lógica de E-mail e Mensagem de Sucesso (MANTIDA) ---
                    resumo_cancelamento = f"""
                    Agendamento Cancelado:
                    Nome: {agendamento_cancelado_data.get('nome', 'N/A')}
                    Telefone: {agendamento_cancelado_data.get('telefone', 'N/A')}
                    Data: {data_cancelar.strftime('%d/%m/%Y')}
                    Horário: {agendamento_cancelado_data.get('horario', 'N/A')}
                    Barbeiro: {agendamento_cancelado_data.get('barbeiro', 'N/A')}
                    Serviços: {', '.join(agendamento_cancelado_data.get('servicos', []))}
                    """
                    enviar_email("Agendamento Cancelado", resumo_cancelamento)
        
                    st.success("Agendamento cancelado com sucesso!")
                    if horario_seguinte_desbloqueado:
                        st.info("O horário seguinte, que estava bloqueado, foi liberado.")
        
                    time.sleep(5)
                    st.rerun()










