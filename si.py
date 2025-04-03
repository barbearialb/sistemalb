import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
import json
import google.api_core.exceptions
import google.api_core.retry as retry
import random
import pandas as pd
import time

st.markdown(
    """
    <style>
        table {
            display: block !important;
            width: fit-content !important; /* Ou tente width: -webkit-fill-available !important; */
        }
        div[data-testid="stForm"] {
            display: block !important;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

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
    "Tradicional": 15,
    "Social": 18,
    "Degradê": 23,
    "Navalhado": 25,
    "Pezim": 7,
    "Barba": 15,
    "Abordagem de visagismo": 45,
    "Consultoria de visagismo": 65,
}

# Lista de serviços para exibição
lista_servicos = list(servicos.keys())

barbeiros = ["Lucas Borges", "Aluizio"]

# Função para enviar e-mail
def enviar_email(assunto, mensagem):
    # Verificar se as credenciais de e-mail foram carregadas
    if not EMAIL or not SENHA:
        st.error("Credenciais de e-mail não configuradas nos secrets.")
        return
    try:
        msg = MIMEText(mensagem)
        msg['Subject'] = assunto
        msg['From'] = EMAIL
        msg['To'] = EMAIL # Enviando para o próprio e-mail da barbearia

        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL, SENHA)  # Login usando as credenciais do e-mail
            server.sendmail(EMAIL, EMAIL, msg.as_string())
            # st.info(f"E-mail de '{assunto}' enviado para {EMAIL}") # Log opcional
    except Exception as e:
        st.error(f"Erro ao enviar e-mail: {e}")

def salvar_agendamento(data, horario, nome, telefone, servicos, barbeiro):
    if not db:
        st.error("Conexão com Firestore não estabelecida.")
        return False # Indica falha
    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)

    # Converter a string de data para um objeto datetime.datetime
    try:
        data_obj = datetime.strptime(data, '%d/%m/%Y')
    except ValueError:
        st.error("Formato de data inválido ao tentar salvar.")
        return False # Indica falha

    @firestore.transactional
    def atualizar_agendamento(transaction, ref, dados):
        doc = ref.get(transaction=transaction)
        if doc.exists:
            raise ValueError("Horário já ocupado.")
        transaction.set(ref, dados)

    transaction = db.transaction()
    try:
        dados_agendamento = {
            'nome': nome,
            'telefone': telefone,
            'servicos': servicos,
            'barbeiro': barbeiro,
            'data': data_obj,  # Salvar o objeto datetime.datetime no Firestore
            'horario': horario,
            'timestamp_criacao': firestore.SERVER_TIMESTAMP # Adiciona um timestamp
        }
        atualizar_agendamento(transaction, agendamento_ref, dados_agendamento)
        # st.info("Agendamento salvo com sucesso na transação.") # Log opcional
        return True # Indica sucesso
    except ValueError as e:
        st.error(f"Erro ao salvar agendamento: {e}")
        return False # Indica falha
    except Exception as e:
        st.error(f"Erro inesperado ao salvar agendamento: {e}")
        return False # Indica falha

# Função para cancelar agendamento no Firestore
def cancelar_agendamento(data, horario, telefone, barbeiro):
    if not db:
        st.error("Conexão com Firestore não estabelecida.")
        return None
    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    try:
        doc = agendamento_ref.get()
        if doc.exists:
            agendamento_data = doc.to_dict()
            # Verifica se o telefone coincide (importante para segurança)
            if agendamento_data.get('telefone') == telefone:
                # Formata a data de volta para string se necessário (embora o ideal seja manter como timestamp/datetime)
                data_do_agendamento = agendamento_data.get('data')
                if isinstance(data_do_agendamento, datetime):
                     agendamento_data['data_formatada'] = data_do_agendamento.strftime('%d/%m/%Y')
                else:
                     # Tentar tratar como string se não for datetime (menos ideal)
                     try:
                         agendamento_data['data_formatada'] = datetime.strptime(str(data_do_agendamento), '%Y-%m-%d %H:%M:%S').strftime('%d/%m/%Y')
                     except:
                          agendamento_data['data_formatada'] = str(data_do_agendamento) # Fallback

                agendamento_ref.delete()
                return agendamento_data # Retorna os dados do agendamento cancelado
            else:
                # Telefone não coincide
                return "telefone_invalido"
        else:
            # Agendamento não encontrado
            return None
    except Exception as e:
        st.error(f"Erro ao acessar o Firestore para cancelamento: {e}")
        return None


# Função para desbloquear o horário seguinte (se estava bloqueado)
def desbloquear_horario(data, horario, barbeiro):
    if not db:
        st.error("Conexão com Firestore não estabelecida ao tentar desbloquear.")
        return
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO"
    bloqueio_ref = db.collection('agendamentos').document(chave_bloqueio)
    try:
        doc = bloqueio_ref.get()
        if doc.exists and doc.to_dict().get('nome') == "BLOQUEADO":
            # print(f"Tentando excluir a chave de bloqueio: {chave_bloqueio}") # Log opcional
            bloqueio_ref.delete()
            # print(f"Horário {horario} do barbeiro {barbeiro} na data {data} desbloqueado.") # Log opcional
        # else:
            # print(f"Documento de bloqueio não encontrado ou inválido para {chave_bloqueio}") # Log opcional
    except Exception as e:
        st.error(f"Erro ao tentar desbloquear horário {horario} para {barbeiro}: {e}")


# Função para verificar disponibilidade do horário no Firebase
# Usar cache aqui pode ser problemático se a disponibilidade mudar rapidamente
# @st.cache_data # Removido cache para sempre buscar o estado mais atual
def verificar_disponibilidade(data_str, horario, barbeiro=None):
    # print(f"Verificando disp: {data_str}, {horario}, {barbeiro}") # Log para depuração
    if not db:
        st.error("Firestore não inicializado.")
        return False # Indisponível se não há DB

    # Verificar agendamento regular
    chave_agendamento = f"{data_str}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)

    # Verificar horário bloqueado explicitamente
    chave_bloqueio = f"{data_str}_{horario}_{barbeiro}_BLOQUEADO"
    bloqueio_ref = db.collection('agendamentos').document(chave_bloqueio)

    try:
        doc_agendamento = agendamento_ref.get()
        doc_bloqueio = bloqueio_ref.get()
        # print(f"  Agendamento existe: {doc_agendamento.exists}") # Log
        # print(f"  Bloqueio existe: {doc_bloqueio.exists}") # Log
        disponivel = not doc_agendamento.exists and not doc_bloqueio.exists
        # print(f"  Resultado disponibilidade: {disponivel}") # Log
        return disponivel
    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore ao verificar disponibilidade: {e}")
        return False # Assume indisponível em caso de erro de conexão
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade ({data_str}, {horario}, {barbeiro}): {e}")
        return False # Assume indisponível em caso de erro geral

# Função para verificar disponibilidade do horário e do horário seguinte
# @retry.Retry() # Retry pode mascarar problemas persistentes, usar com cautela
def verificar_disponibilidade_horario_seguinte(data_str, horario, barbeiro):
    if not db:
        st.error("Firestore não inicializado.")
        return False
    try:
        horario_dt = datetime.strptime(horario, '%H:%M')
        horario_seguinte_dt = horario_dt + timedelta(minutes=30)
        # Verificar se o horário seguinte ainda está dentro do expediente (ex: antes das 20:00)
        if horario_seguinte_dt.hour >= 20:
             # print(f"Horário seguinte {horario_seguinte_dt.strftime('%H:%M')} está fora do expediente.") # Log opcional
             return False # Fora do expediente, não pode agendar serviço duplo aqui
        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')

        # print(f"Verificando disp. seguinte: {data_str}, {horario_seguinte_str}, {barbeiro}") # Log
        return verificar_disponibilidade(data_str, horario_seguinte_str, barbeiro)
    except ValueError:
        st.error("Erro ao calcular horário seguinte.")
        return False
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade do horário seguinte: {e}")
        return False

# Função para bloquear horário explicitamente para um barbeiro específico
def bloquear_horario_explicitamente(data_str, horario, barbeiro):
    if not db:
        st.error("Conexão com Firestore não estabelecida ao tentar bloquear.")
        return False
    chave_bloqueio = f"{data_str}_{horario}_{barbeiro}_BLOQUEADO"
    bloqueio_ref = db.collection('agendamentos').document(chave_bloqueio)
    try:
        # Converter a string de data para um objeto datetime.datetime para salvar no Firestore
        data_obj = datetime.strptime(data_str, '%d/%m/%Y')

        bloqueio_ref.set({
            'nome': "BLOQUEADO",
            'telefone': "BLOQUEADO",
            'servicos': ["BLOQUEADO"],
            'barbeiro': barbeiro,
            'data': data_obj, # Salva como datetime
            'horario': horario,
            'timestamp_criacao': firestore.SERVER_TIMESTAMP
        })
        # print(f"Horário {horario} bloqueado explicitamente para {barbeiro} em {data_str}.") # Log opcional
        return True
    except ValueError:
        st.error("Formato de data inválido ao tentar bloquear horário.")
        return False
    except Exception as e:
        st.error(f"Erro ao bloquear horário {horario} para {barbeiro}: {e}")
        return False


# --- Interface Streamlit ---
st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/sistemalb/blob/main/icone.png?raw=true", use_container_width=True)

# Gerenciamento da data selecionada usando session_state
if 'data_selecionada' not in st.session_state:
    st.session_state.data_selecionada = datetime.today().date() # Inicializar como objeto date

# Widget de data - Sempre visível
data_input = st.date_input(
    "Selecione a data",
    min_value=datetime.today().date(), # Usa .date() para min_value
    key="data_widget", # Chave única para o widget
    value=st.session_state.data_selecionada # Usa o valor do state
)

# Atualiza o session state se a data no widget mudar
if data_input != st.session_state.data_selecionada:
    st.session_state.data_selecionada = data_input
    # verificar_disponibilidade.clear() # Limpa o cache se a data mudar (cache removido)
    st.rerun() # Força o recarregamento para atualizar a tabela com a nova data

# Formatar a data selecionada (do session_state) para uso nas funções e exibição
data_para_uso = st.session_state.data_selecionada.strftime('%d/%m/%Y')

# --- Tabela de Disponibilidade ---
st.subheader(f"Disponibilidade para: {data_para_uso}")

# Cabeçalho da tabela HTML
html_table = '<table style="font-size: 14px; border-collapse: collapse; width: 100%; border: 1px solid #ddd;"><tr><th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">Horário</th>'
for barbeiro in barbeiros:
    html_table += f'<th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">{barbeiro}</th>'
html_table += '</tr>'

# Gerar horários base dinamicamente para a tabela
horarios_tabela = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]
dia_da_semana_selecionado = st.session_state.data_selecionada.weekday() # 0 = segunda, 6 = domingo

# Preencher linhas da tabela
for horario in horarios_tabela:
    html_table += f'<tr><td style="padding: 8px; border: 1px solid #ddd;">{horario}</td>'
    hora_int = int(horario.split(':')[0])
    minuto_int = int(horario.split(':')[1])

    for barbeiro in barbeiros:
        status = "Indisponível" # Default
        bg_color = "grey"      # Default
        color_text = "white"   # Default

        # Lógica de Horário de Almoço (Segunda a Sexta)
        em_almoco = False
        if dia_da_semana_selecionado < 5: # Segunda a Sexta
            # Almoço Lucas: 12:00 e 12:30
            if barbeiro == "Lucas Borges" and hora_int == 12 and (minuto_int == 0 or minuto_int == 30):
                em_almoco = True
            # Almoço Aluizio: 13:00 e 13:30
            elif barbeiro == "Aluizio" and hora_int == 13 and (minuto_int == 0 or minuto_int == 30):
                em_almoco = True

        if em_almoco:
            status = "Almoço"
            bg_color = "orange"
            color_text = "black"
        else:
            # Se não está em almoço, verifica disponibilidade no Firestore
            disponivel = verificar_disponibilidade(data_para_uso, horario, barbeiro)
            if disponivel:
                status = "Disponível"
                bg_color = "forestgreen"
                color_text = "white"
            else:
                # Se não está disponível e não é almoço, está ocupado
                status = "Ocupado"
                bg_color = "firebrick"
                color_text = "white"

        # Adiciona a célula à tabela HTML
        html_table += f'<td style="padding: 8px; border: 1px solid #ddd; background-color: {bg_color}; text-align: center; color: {color_text}; height: 30px;">{status}</td>'
    html_table += '</tr>' # Fecha a linha do horário

html_table += '</table>'
st.markdown(html_table, unsafe_allow_html=True)


# --- Formulário de Agendamento ---
with st.form("agendar_form", clear_on_submit=True): # Limpa o form após submeter
    st.subheader("Agendar Horário")
    nome = st.text_input("Nome Completo")
    telefone = st.text_input("Telefone (com DDD, ex: 11987654321)")

    # A data usada é a `data_para_uso` definida a partir do session_state
    st.write(f"Data selecionada para agendamento: **{data_para_uso}**")

    # Gerar horários base para o selectbox de agendamento
    horarios_base_agendamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

    barbeiro_selecionado_form = st.selectbox("Escolha o barbeiro", barbeiros + ["Sem preferência"], key="form_barbeiro")

    # Filtrar horários base para o selectbox (opcionalmente remover almoço, mas verificar disponibilidade é mais robusto)
    horarios_para_selectbox = horarios_base_agendamento # Por padrão, mostrar todos
    # Aqui poderíamos adicionar filtros se quiséssemos esconder os horários de almoço do selectbox,
    # mas a verificação final na submissão é mais importante.

    horario_agendamento = st.selectbox("Horário desejado", horarios_para_selectbox, key="form_horario")

    servicos_selecionados = st.multiselect("Serviços desejados", lista_servicos, key="form_servicos")

    # Exibir os preços com o símbolo R$ (opcional, pode poluir o form)
    # st.write("--- Preços ---")
    # servicos_com_preco = {servico: f"R$ {preco}" for servico, preco in servicos.items()}
    # for servico, preco_str in servicos_com_preco.items():
    #    st.write(f"{servico}: {preco_str}")
    # st.write("---")

    submitted_agendar = st.form_submit_button("Confirmar Agendamento")

# --- Processamento do Agendamento ---
if submitted_agendar:
    erro_agendamento = False
    msg_erro = ""

    # Validações básicas
    if not nome:
        msg_erro += "Nome é obrigatório.\n"
        erro_agendamento = True
    if not telefone or not telefone.isdigit() or len(telefone) < 10: # Validação simples de telefone
         msg_erro += "Telefone inválido (use apenas números, incluindo DDD).\n"
         erro_agendamento = True
    if not servicos_selecionados:
        msg_erro += "Selecione pelo menos um serviço.\n"
        erro_agendamento = True
    if not horario_agendamento:
        msg_erro += "Selecione um horário.\n"
        erro_agendamento = True
    if not barbeiro_selecionado_form:
         msg_erro += "Selecione um barbeiro ou 'Sem preferência'.\n"
         erro_agendamento = True

    # Se houver erros básicos, exibe e para
    if erro_agendamento:
        st.error("Por favor, corrija os seguintes erros:\n" + msg_erro)
    else:
        with st.spinner("Verificando disponibilidade e processando..."):
            data_agendamento_str = data_para_uso # Usar a data já formatada
            hora_agendamento_obj = datetime.strptime(horario_agendamento, '%H:%M')
            dia_da_semana_ag = datetime.strptime(data_agendamento_str, '%d/%m/%Y').weekday()

            # --- Lógica de Seleção de Barbeiro e Verificação ---
            barbeiro_final = None
            disponibilidade_ok = False
            horario_seguinte_ok = True # Assume que ok por padrão, verifica se necessário
            necessita_horario_seguinte = "Barba" in servicos_selecionados and any(corte in servicos_selecionados for corte in ["Tradicional", "Social", "Degradê", "Navalhado", "Abordagem de visagismo", "Consultoria de visagismo"])

            # 1. Verificar se o serviço de visagismo exige Lucas Borges
            servicos_visagismo = ["Abordagem de visagismo", "Consultoria de visagismo"]
            visagismo_selecionado = any(s in servicos_selecionados for s in servicos_visagismo)

            if visagismo_selecionado and barbeiro_selecionado_form == "Aluizio":
                 st.error("Serviços de visagismo são realizados apenas por Lucas Borges.")
                 st.stop() # Para a execução aqui
            elif visagismo_selecionado and barbeiro_selecionado_form == "Sem preferência":
                 barbeiro_selecionado_form = "Lucas Borges" # Força Lucas se visagismo e sem preferência
                 # print("Visagismo selecionado, 'Sem preferência' alterado para Lucas Borges.") # Log

            # 2. Determinar o barbeiro final e verificar disponibilidade
            if barbeiro_selecionado_form == "Sem preferência":
                # Tentar atribuir a um barbeiro disponível aleatoriamente
                barbeiros_disponiveis_agora = [b for b in barbeiros if verificar_disponibilidade(data_agendamento_str, horario_agendamento, b)]

                if not barbeiros_disponiveis_agora:
                    st.error(f"Desculpe, nenhum barbeiro está disponível às {horario_agendamento} em {data_agendamento_str}. Por favor, escolha outro horário ou data.")
                    st.stop()
                else:
                    # Tenta priorizar um que tenha o próximo horário livre se necessário
                    barbeiro_escolhido_temp = None
                    if necessita_horario_seguinte:
                        for b_disp in barbeiros_disponiveis_agora:
                             if verificar_disponibilidade_horario_seguinte(data_agendamento_str, horario_agendamento, b_disp):
                                 barbeiro_escolhido_temp = b_disp
                                 break # Encontrou um bom
                        if not barbeiro_escolhido_temp:
                            st.error(f"Os barbeiros disponíveis às {horario_agendamento} não têm o horário seguinte livre para o(s) serviço(s) selecionado(s). Tente outro horário ou barbeiro específico.")
                            st.stop()
                    else:
                        # Se não precisa do próximo horário, pega qualquer um disponível
                         barbeiro_escolhido_temp = random.choice(barbeiros_disponiveis_agora)

                    barbeiro_final = barbeiro_escolhido_temp
                    disponibilidade_ok = True # Já verificado ao popular barbeiros_disponiveis_agora
                    # print(f"'Sem preferência' atribuído a: {barbeiro_final}") # Log

            else:
                # Barbeiro específico foi selecionado
                barbeiro_final = barbeiro_selecionado_form
                # Verificar almoço do barbeiro selecionado
                em_almoco_selecionado = False
                hora_int_ag = hora_agendamento_obj.hour
                min_int_ag = hora_agendamento_obj.minute
                if dia_da_semana_ag < 5: # Seg a Sex
                    if barbeiro_final == "Lucas Borges" and hora_int_ag == 12 and (min_int_ag == 0 or min_int_ag == 30):
                        em_almoco_selecionado = True
                    elif barbeiro_final == "Aluizio" and hora_int_ag == 13 and (min_int_ag == 0 or min_int_ag == 30):
                        em_almoco_selecionado = True

                if em_almoco_selecionado:
                     st.error(f"{barbeiro_final} está em horário de almoço às {horario_agendamento}. Escolha outro horário.")
                     st.stop()

                # Verificar disponibilidade geral do barbeiro selecionado
                disponibilidade_ok = verificar_disponibilidade(data_agendamento_str, horario_agendamento, barbeiro_final)
                if not disponibilidade_ok:
                     st.error(f"Desculpe, o horário das {horario_agendamento} com {barbeiro_final} já está ocupado. Consulte a tabela de disponibilidade.")
                     st.stop()

            # 3. Se o barbeiro foi definido e o horário está vago, verificar o próximo horário se necessário
            if disponibilidade_ok and necessita_horario_seguinte:
                horario_seguinte_ok = verificar_disponibilidade_horario_seguinte(data_agendamento_str, horario_agendamento, barbeiro_final)
                if not horario_seguinte_ok:
                    horario_seguinte_str_err = (hora_agendamento_obj + timedelta(minutes=30)).strftime('%H:%M')
                    st.error(f"{barbeiro_final} não tem o horário seguinte ({horario_seguinte_str_err}) livre para realizar todos os serviços selecionados. Tente outro horário ou barbeiro.")
                    st.stop()

            # 4. Se passou por todas as verificações, salvar e bloquear
            if disponibilidade_ok and horario_seguinte_ok and barbeiro_final:
                # Salvar o agendamento principal
                salvo_com_sucesso = salvar_agendamento(data_agendamento_str, horario_agendamento, nome, telefone, servicos_selecionados, barbeiro_final)

                if salvo_com_sucesso:
                    # Bloquear horário seguinte se necessário
                    if necessita_horario_seguinte:
                         horario_seguinte_str_ok = (hora_agendamento_obj + timedelta(minutes=30)).strftime('%H:%M')
                         bloqueado = bloquear_horario_explicitamente(data_agendamento_str, horario_seguinte_str_ok, barbeiro_final)
                         if not bloqueado:
                              st.warning(f"Agendamento principal salvo, mas houve um erro ao bloquear o horário seguinte ({horario_seguinte_str_ok}). Por favor, informe à barbearia.")
                         # else:
                              # print(f"Horário seguinte {horario_seguinte_str_ok} bloqueado para {barbeiro_final}.") # Log

                    # Preparar e enviar e-mail de confirmação
                    resumo = f"""
                    Novo Agendamento Confirmado:
                    -------------------------
                    Nome: {nome}
                    Telefone: {telefone}
                    Data: {data_agendamento_str}
                    Horário: {horario_agendamento}
                    Barbeiro: {barbeiro_final}
                    Serviços: {', '.join(servicos_selecionados)}
                    """
                    enviar_email(f"Novo Agendamento: {nome} - {data_agendamento_str} {horario_agendamento}", resumo)

                    # Mensagem de sucesso e rerun
                    st.success(f"Agendamento confirmado com sucesso para {nome} com {barbeiro_final} em {data_agendamento_str} às {horario_agendamento}!")
                    if necessita_horario_seguinte and bloqueado:
                        st.info(f"O horário seguinte ({horario_seguinte_str_ok}) também foi reservado para o(s) serviço(s).")
                    st.balloons()
                    time.sleep(5) # Pausa para o usuário ler
                    # verificar_disponibilidade.clear() # Limpa cache se existir (removido)
                    st.rerun() # Recarrega a página para atualizar a tabela

                else:
                    # Se salvar_agendamento retornou False
                    st.error("Ocorreu um erro ao tentar salvar o agendamento no banco de dados. Tente novamente.")
                    # Não precisa de st.stop() aqui, pois o fluxo normal termina

            else:
                 # Se alguma verificação anterior falhou (já deve ter parado com st.stop)
                 st.error("Não foi possível concluir o agendamento devido a um erro de validação ou disponibilidade.")


# --- Formulário de Cancelamento ---
with st.form("cancelar_form", clear_on_submit=True):
    st.subheader("Cancelar Agendamento")
    telefone_cancelar = st.text_input("Seu Telefone (usado no agendamento)")
    # Usa a mesma data selecionada no widget principal para conveniência inicial
    st.write(f"Data selecionada para cancelamento: **{data_para_uso}**")
    # Permitir mudar a data especificamente para cancelamento, se necessário:
    data_cancelar_input = st.date_input("Confirme/Altere a Data do Agendamento a Cancelar", value=st.session_state.data_selecionada, min_value=datetime.today().date(), key="cancel_date")
    data_cancelar_str = data_cancelar_input.strftime('%d/%m/%Y')


    # Geração da lista de horários completa para cancelamento
    horarios_base_cancelamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

    # --- CORREÇÃO APLICADA AQUI ---
    # Criar a lista de horários para o dropdown de cancelamento
    horarios_filtrados_cancelamento = []
    for horario in horarios_base_cancelamento:
        # CORREÇÃO: Adicionar o horário à lista CORRETA
        horarios_filtrados_cancelamento.append(horario)
    # --- FIM DA CORREÇÃO ---

    # Agora a lista não está mais vazia
    horario_cancelar = st.selectbox("Horário do Agendamento a Cancelar", horarios_filtrados_cancelamento, key="cancel_time")

    barbeiro_cancelar = st.selectbox("Barbeiro do Agendamento a Cancelar", barbeiros, key="cancel_barber")

    submitted_cancelar = st.form_submit_button("Confirmar Cancelamento")

# --- Processamento do Cancelamento ---
if submitted_cancelar:
    if not telefone_cancelar or not telefone_cancelar.isdigit() or len(telefone_cancelar) < 10:
        st.error("Por favor, insira um número de telefone válido (somente números, com DDD) usado no agendamento.")
    else:
        with st.spinner("Processando cancelamento..."):
            resultado_cancelamento = cancelar_agendamento(data_cancelar_str, horario_cancelar, telefone_cancelar, barbeiro_cancelar)

            if resultado_cancelamento == "telefone_invalido":
                 st.error(f"Agendamento encontrado para {data_cancelar_str} às {horario_cancelar} com {barbeiro_cancelar}, mas o telefone informado não corresponde ao do agendamento.")
            elif resultado_cancelamento is None:
                 st.error(f"Nenhum agendamento encontrado para os dados informados (Telefone: {telefone_cancelar}, Data: {data_cancelar_str}, Horário: {horario_cancelar}, Barbeiro: {barbeiro_cancelar}). Verifique os dados e tente novamente.")
            elif isinstance(resultado_cancelamento, dict): # Sucesso
                 cancelado = resultado_cancelamento # Renomeia para clareza
                 # Prepara e envia e-mail de cancelamento
                 resumo_cancelamento = f"""
                 Agendamento Cancelado:
                 --------------------
                 Nome: {cancelado.get('nome', 'N/A')}
                 Telefone: {cancelado.get('telefone', 'N/A')}
                 Data: {cancelado.get('data_formatada', data_cancelar_str)}
                 Horário: {cancelado.get('horario', 'N/A')}
                 Barbeiro: {cancelado.get('barbeiro', 'N/A')}
                 Serviços: {', '.join(cancelado.get('servicos', []))}
                 """
                 enviar_email(f"Cancelamento: {cancelado.get('nome', 'N/A')} - {cancelado.get('data_formatada', data_cancelar_str)} {cancelado.get('horario', 'N/A')}", resumo_cancelamento)

                 # Desbloquear horário seguinte, se aplicável
                 servicos_cancelados = cancelado.get('servicos', [])
                 necessitava_horario_seguinte = "Barba" in servicos_cancelados and any(corte in servicos_cancelados for corte in ["Tradicional", "Social", "Degradê", "Navalhado", "Abordagem de visagismo", "Consultoria de visagismo"])

                 if necessitava_horario_seguinte:
                     try:
                         horario_cancelado_dt = datetime.strptime(cancelado['horario'], '%H:%M')
                         horario_seguinte_cancelado_str = (horario_cancelado_dt + timedelta(minutes=30)).strftime('%H:%M')
                         # A data usada para desbloquear deve ser a mesma do agendamento cancelado (já formatada como string)
                         desbloquear_horario(cancelado.get('data_formatada', data_cancelar_str), horario_seguinte_cancelado_str, cancelado['barbeiro'])
                         st.info(f"O horário seguinte ({horario_seguinte_cancelado_str}) que estava bloqueado foi liberado.")
                     except Exception as e_desbloq:
                         st.warning(f"Agendamento cancelado, mas houve erro ao tentar desbloquear horário seguinte: {e_desbloq}")


                 st.success("Agendamento cancelado com sucesso!")
                 # st.info("Resumo do cancelamento:\n" + resumo_cancelamento) # Opcional mostrar resumo na tela
                 time.sleep(5) # Pausa para o usuário ler
                 # verificar_disponibilidade.clear() # Limpa cache (removido)
                 st.rerun()
            else:
                 # Caso inesperado
                 st.error("Ocorreu um erro desconhecido durante o cancelamento.")
