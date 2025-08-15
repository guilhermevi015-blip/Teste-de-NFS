# -*- coding: utf-8 -*-

import streamlit as st
import fitz  # PyMuPDF
import re
import sqlite3
import os
from datetime import datetime
import pandas as pd

# --- Configurações Iniciais do Banco de Dados (executa uma vez) ---
# O @st.cache_resource garante que a conexão não seja refeita a cada interação
@st.cache_resource
def setup_database():
    conn = sqlite3.connect('historico_analises_web.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analises (
            id INTEGER PRIMARY KEY AUTOINCREMENT, data_processamento TEXT NOT NULL, nome_arquivo TEXT NOT NULL,
            faturamento REAL NOT NULL, pis_liquido REAL NOT NULL, cofins_liquido REAL NOT NULL,
            csll_liquido REAL NOT NULL, irpj_liquido REAL NOT NULL, iss_liquido REAL NOT NULL, inss_retido REAL NOT NULL
        )
    ''')
    conn.commit()
    return conn

# --- Funções de Lógica (Nosso "motor" já validado) ---
# O @st.cache_data ajuda a não reprocessar o mesmo arquivo repetidamente
@st.cache_data
def extract_data_from_pdf(pdf_bytes):
    # Modificado para ler bytes em vez de um caminho de arquivo
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    invoices_with_retention = []
    # (O restante da função de extração é EXATAMENTE o mesmo de antes)
    anchor_map = {
        'numero_nota': 'Número da NFS-e', 'data_emissao': 'Data e Hora de Emissão da NFS-e',
        'cnpj_tomador': 'CPF/CNPJ/Documento', 'razao_social_tomador': 'Nome/Razão Social',
        'iss_retido_check': 'ISS Retido', 'valor_iss': 'Total do ISS', 'valor_pis': 'PIS',
        'valor_cofins': 'COFINS', 'valor_csll': 'CSLL', 'valor_irrf': 'IRRF', 'valor_inss': 'INSS',
    }
    for page_num in range(len(document)):
        page = document.load_page(page_num)
        lines = page.get_text("text").split('\n')
        invoice_data = {}
        for i, line in enumerate(lines):
            for key, anchor in anchor_map.items():
                if anchor.strip().lower() == line.strip().lower() and (i + 1) < len(lines):
                    invoice_data[key] = lines[i + 1].strip()
        # ... (lógica de clean_currency e verificação de retenção)
        pis = clean_currency(invoice_data.get('valor_pis')); cofins = clean_currency(invoice_data.get('valor_cofins'))
        csll = clean_currency(invoice_data.get('valor_csll')); irrf = clean_currency(invoice_data.get('valor_irrf'))
        inss = clean_currency(invoice_data.get('valor_inss')); iss = clean_currency(invoice_data.get('valor_iss'))
        iss_retido = '1' in invoice_data.get('iss_retido_check', '')
        valor_final_iss = iss if iss_retido else 0.0
        if any([pis > 0, cofins > 0, csll > 0, irrf > 0, inss > 0, iss_retido]):
            invoices_with_retention.append({
                'PIS Retido': pis, 'COFINS Retido': cofins, 'CSLL Retido': csll, 'IRRF Retido': irrf, 'INSS Retido': inss, 'ISS Retido': valor_final_iss
            })
    return invoices_with_retention

def clean_currency(text):
    if not text: return 0.0
    try:
        match = re.search(r'[\d\.,]+', text)
        if match:
            value_str = match.group(0).replace('.', '').replace(',', '.')
            return float(value_str)
    except (ValueError, AttributeError): return 0.0
    return 0.0

def calcular_impostos_finais(dados_extraidos, faturamento_mensal):
    # (Esta função é EXATAMENTE a mesma de antes)
    ALIQUOTAS = {'PIS': 0.0065, 'COFINS': 0.03, 'IRPJ': 0.012, 'CSLL': 0.0108, 'ISS': 0.05}
    total_retencoes = {
        'PIS': sum(nota['PIS Retido'] for nota in dados_extraidos), 'COFINS': sum(nota['COFINS Retido'] for nota in dados_extraidos),
        'CSLL': sum(nota['CSLL Retido'] for nota in dados_extraidos), 'IRRF': sum(nota['IRRF Retido'] for nota in dados_extraidos),
        'INSS': sum(nota['INSS Retido'] for nota in dados_extraidos), 'ISS': sum(nota['ISS Retido'] for nota in dados_extraidos)
    }
    resultado_final = {}
    for imposto, aliquota in ALIQUOTAS.items():
        valor_bruto = faturamento_mensal * aliquota
        chave_retencao = 'IRRF' if imposto == 'IRPJ' else imposto
        total_retido = total_retencoes.get(chave_retencao, 0.0)
        valor_liquido = valor_bruto - total_retido
        resultado_final[imposto] = {
            'Aliquota': f"{aliquota:.2%}", 'Valor Bruto a Pagar': valor_bruto,
            'Total Retido': total_retido, 'Valor Liquido a Pagar': valor_liquido
        }
    return resultado_final, total_retencoes.get('INSS', 0.0)

# --- Função de Autenticação ---
def check_password():
    """Retorna True se a senha estiver correta, False caso contrário."""
    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False

    if not st.session_state.password_correct:
        # Layout para centralizar o campo de senha
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.header("Login de Acesso")
            password = st.text_input("Digite a senha:", type="password")
            if st.button("Entrar"):
                # A senha real será configurada como um "secret" no servidor
                if password == st.secrets["APP_PASSWORD"]:
                    st.session_state.password_correct = True
                    st.experimental_rerun()
                else:
                    st.error("A senha está incorreta.")
        return False
    else:
        return True

# --- Interface Principal da Aplicação ---
st.set_page_config(page_title="Analisador Fiscal", layout="wide")

if check_password():
    conn = setup_database()
    st.title("🚀 Analisador de Retenções Fiscais")

    tab1, tab2 = st.tabs(["Nova Análise", "Histórico de Análises"])

    with tab1:
        st.header("1. Faça o upload do arquivo PDF")
        uploaded_file = st.file_uploader("Selecione o arquivo PDF com as notas fiscais", type="pdf")

        st.header("2. Informe o faturamento")
        faturamento = st.number_input("Digite o valor total do faturamento do mês (ex: 100000.50)", min_value=0.0, format="%.2f")

        if st.button("🔍 Analisar Agora", type="primary"):
            if uploaded_file is not None and faturamento > 0:
                with st.spinner('Lendo o PDF e calculando os impostos... Isso pode levar um momento.'):
                    # Lê os bytes do arquivo enviado
                    pdf_bytes = uploaded_file.getvalue()
                    
                    dados_extraidos = extract_data_from_pdf(pdf_bytes)
                    
                    if not dados_extraidos:
                        st.warning("Nenhuma nota com retenção foi encontrada no documento.")
                    else:
                        resumo, inss = calcular_impostos_finais(dados_extraidos, faturamento)

                        # Salva no banco de dados
                        cursor = conn.cursor()
                        cursor.execute('INSERT INTO analises (data_processamento, nome_arquivo, faturamento, pis_liquido, cofins_liquido, csll_liquido, irpj_liquido, iss_liquido, inss_retido) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                                       (datetime.now().strftime('%Y-%m-%d %H:%M'), uploaded_file.name, faturamento, resumo['PIS']['Valor Liquido a Pagar'], resumo['COFINS']['Valor Liquido a Pagar'], resumo['CSLL']['Valor Liquido a Pagar'], resumo['IRPJ']['Valor Liquido a Pagar'], resumo['ISS']['Valor Liquido a Pagar'], inss))
                        conn.commit()

                        st.success("Análise Concluída com Sucesso!")
                        st.subheader("Resumo dos Impostos a Pagar")

                        for imposto, valores in resumo.items():
                            st.markdown(f"**--- {imposto} ({valores['Aliquota']}) ---**")
                            col1, col2, col3 = st.columns(3)
                            col1.metric("Valor Bruto a Pagar", f"R$ {valores['Valor Bruto a Pagar']:,.2f}")
                            col2.metric("Total Retido", f"R$ {valores['Total Retido']:,.2f}")
                            col3.metric("Valor Líquido a Pagar", f"R$ {valores['Valor Liquido a Pagar']:,.2f}")

                        if inss > 0:
                            st.info(f"**Total de INSS Retido (informativo):** R$ {inss:,.2f}")
            else:
                st.error("Por favor, faça o upload de um arquivo PDF e informe um valor de faturamento válido.")

    with tab2:
        st.header("Histórico de Análises")
        try:
            # Carrega os dados do DB para um DataFrame do Pandas para fácil exibição
            df = pd.read_sql_query("SELECT id, data_processamento as 'Data', nome_arquivo as 'Arquivo', faturamento as 'Faturamento', pis_liquido as 'PIS Líquido', cofins_liquido as 'COFINS Líquido', csll_liquido as 'CSLL Líquido', irpj_liquido as 'IRPJ Líquido', iss_liquido as 'ISS Líquido', inss_retido as 'INSS Retido' FROM analises ORDER BY id DESC", conn)
            st.dataframe(df)
        except Exception as e:
            st.error(f"Não foi possível carregar o histórico: {e}")