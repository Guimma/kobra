import gspread
from google.oauth2.service_account import Credentials
import re
import logging
import random

# Configuração do logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Autenticação com a conta de serviço
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

creds = Credentials.from_service_account_file('lofty-mark-407721-c78e211dcebf.json', scopes=SCOPES)
client = gspread.authorize(creds)

# Abrir a planilha pelo ID e acessar a terceira aba
spreadsheet = client.open_by_key('1GRC7L_2Q75OEywLz-gFU6jrcKLhpgA3-1LZ_qFviFsY')
sheet = spreadsheet.get_worksheet(2)  # A terceira aba

# Obter todos os dados da aba
data = sheet.get_all_records()
logging.debug("Dados da planilha carregados com sucesso.")

# Função para buscar o nível e tipo de jogador
def buscar_nivel_e_tipo(apelido):
    apelido = apelido.strip().lower()  # Normaliza o apelido removendo espaços e convertendo para minúsculas
    logging.debug(f"Buscando nível e tipo para o jogador: {apelido}")
    for row in data:
        nome_jogador = row.get('Apelido', '').strip().lower()  # Normaliza o nome na planilha também
        if nome_jogador == apelido:
            nivel = row.get('Nível', 'Nível não encontrado')
            tipo = row.get('Tipo de Jogador', 'Tipo não encontrado')
            logging.debug(f"Nível e tipo encontrados para {apelido}: {nivel}, {tipo}")
            return nivel, tipo
    logging.error(f"Nível e tipo não encontrados para {apelido}")
    return None, None

# Função para processar a lista de jogadores do arquivo
def processar_lista_jogadores(filename):
    logging.info("Iniciando processamento da lista de jogadores.")
    
    # Ler o conteúdo do arquivo lista.txt
    try:
        with open(filename, 'r', encoding='utf-8') as file:
            lista_input = file.read()
            logging.debug(f"Conteúdo do arquivo:\n{lista_input}")
    except FileNotFoundError:
        logging.error(f"O arquivo '{filename}' não foi encontrado.")
        return []

    # Extrair os nomes dos jogadores usando regex para capturar as linhas com o padrão "01. Nome"
    jogadores = re.findall(r"\d{2}\.\s+(.+)", lista_input)
    logging.debug(f"{len(jogadores)} jogadores extraídos da lista: {jogadores}")
    
    # Obter níveis e tipos dos jogadores
    jogadores_com_nivel = [(apelido, *buscar_nivel_e_tipo(apelido)) for apelido in jogadores]
    jogadores_com_nivel = [j for j in jogadores_com_nivel if j[1] is not None]  # Filtrar jogadores com nível válido

    logging.info("Processamento da lista de jogadores concluído.")
    return jogadores_com_nivel

def sortear_times(jogadores, num_times=3, jogadores_por_time=6):
    logging.info("Iniciando o sorteio equilibrado dos times.")
    
    # Agrupar jogadores por nível
    nivel_1 = [j for j in jogadores if j[1] == 1]
    nivel_2 = [j for j in jogadores if j[1] == 2]
    nivel_3 = [j for j in jogadores if j[1] == 3]
    nivel_4 = [j for j in jogadores if j[1] == 4]

    # Embaralhar os jogadores de cada nível
    random.shuffle(nivel_1)
    random.shuffle(nivel_2)
    random.shuffle(nivel_3)
    random.shuffle(nivel_4)

    # Inicializar os times
    times = [[] for _ in range(num_times)]
    niveis_times = [0] * num_times  # Soma dos níveis de cada time

    # Distribuir jogadores de cada nível nos times
    for nivel_jogadores in [nivel_1, nivel_2, nivel_3, nivel_4]:  # Começar pelos melhores
        for jogador in nivel_jogadores:
            # Escolher o time com a menor soma de níveis
            indice_time = niveis_times.index(min(niveis_times))
            times[indice_time].append(jogador)
            niveis_times[indice_time] += jogador[1]

    # Verificar se todos os times têm exatamente o número esperado de jogadores
    for time in times:
        if len(time) != jogadores_por_time:
            logging.error("Erro na distribuição: nem todos os times têm o número esperado de jogadores.")
            return

    # Exibir os times sorteados
    print("*🐍⚽ Kobra FC | Sorteio dos Times*")
    print("_🤖 Sorteio Automatizado por Bot, com nivelamento para equilibrar os times. 🤖_\n")
    cores = ["🔴 *Time Vermelho*", "⚪ *Time Branco*", "🔵 *Time Azul*"]
    
    for i, time in enumerate(times):
        print(f"{cores[i]}")
        for j, (jogador, nivel, tipo) in enumerate(time):
            status_emoji = "✅" if tipo == "Mensalista" else "✖️"
            # print(f"{j + 1}. {jogador} {status_emoji} ({nivel})")
            print(f"{j + 1}. {jogador} {status_emoji}")
        print()

    print("\n_ℹ Após realizar o pagamento, copie a mensagem acima e coloque um ✅ a frente do seu nome. Os times que pagarem primeiro começam jogando._\n")
    print("*FAVOR PAGAR ANTES DO INÍCIO DA PELADA!*\n")
    print("_Mensalista: 💵 R$ 80,00 na primeira pelada do mês_")
    print("_Avulso: 💵 R$ 23,00_")
    print("_Pix: 12685405607_")

# Atualize o arquivo 'lista.txt' conforme necessário e execute o sorteio:
jogadores = processar_lista_jogadores('lista.txt')
if len(jogadores) == 18:
    sortear_times(jogadores)
else:
    logging.error(f"Jogadores Insuficientes. Apenas {len(jogadores)} encontrados.")