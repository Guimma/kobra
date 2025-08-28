import gspread
from google.oauth2.service_account import Credentials
import re
import logging
import random
import pulp

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

# Define the expected headers based on the unique columns you need
expected_headers = [
    'ID', 'Nome do Jogador', 'Apelido', 'Tipo de Jogador', 'Nível',
    'Nível backup', '4 Níveis', 'MODA FORMS', 'MEDIANA FORMS',
    'MEDIA FORMS', 'NOVO FORMS MODA', 'Mensalista', 'Status', 'Fila Mensal'
]

# Obter todos os dados da aba com expected_headers
data = sheet.get_all_records(expected_headers=expected_headers)
logging.debug("Dados da planilha carregados com sucesso com expected_headers.")

# Helper to compute alpha weight for per-level distribution penalty
def _compute_alpha_level_dist(player_levels, num_times, total_players):
    """Choose a soft penalty weight based on the day's level distribution.

    - Prioritize average equality elsewhere. Here we only tune tie-breaking across
      solutions with similar z.
    - Heuristic:
      * If N=18 and each level count in [3,6] (well-spread for 3 teams): alpha=0.20
      * Else if N<18 or any level extremely skewed (count <2 or >8): alpha=0.07
      * Else: alpha=0.10
    """
    counts = {lvl: player_levels.count(lvl) for lvl in (1, 2, 3, 4)}
    if total_players == 18 and all(3 <= c <= 6 for c in counts.values()):
        return 0.20, counts
    if total_players < 18 or any(c < 2 or c > 8 for c in counts.values()):
        return 0.07, counts
    return 0.10, counts

# Função para buscar o nível e tipo de jogador
def buscar_nivel_e_tipo(apelido):
    apelido = apelido.lower()
    logging.debug("Fetching level and type for player: %s", apelido)
    for row in data:
        nome_jogador = row.get('Apelido', '').lower()
        if nome_jogador == apelido:
            nivel = row.get('NOVO FORMS MODA', 'Nível não encontrado')
            tipo = row.get('Tipo de Jogador', 'Tipo não encontrado')
            if nivel == '':
                nivel = None
            else:
                try:
                    nivel = int(nivel)
                except Exception:
                    try:
                        nivel = int(float(nivel))
                    except Exception:
                        logging.warning("Unable to convert level for %s: %s", apelido, str(nivel))
                        nivel = None
            logging.debug("Level and type found for %s: %s, %s", apelido, str(nivel), str(tipo))
            return nivel, tipo
    logging.error("Level and type not found for %s", apelido)
    return None, None

# Função para processar a lista de jogadores do arquivo
def processar_lista_jogadores(filename):
    logging.info("Starting player list processing.")
    try:
        with open(filename, 'r', encoding='utf-8') as file:
            lista_input = file.read()
            logging.debug("File content:\n%s", lista_input)
    except FileNotFoundError:
        logging.error("File '%s' was not found.", filename)
        return []

    jogadores = re.findall(r"\d{2}\.\s+(.+)", lista_input)
    logging.debug("%d players extracted from list: %s", len(jogadores), jogadores)

    jogadores_com_nivel = [(apelido, *buscar_nivel_e_tipo(apelido)) for apelido in jogadores]
    jogadores_com_nivel = [j for j in jogadores_com_nivel if j[1] is not None and j[1] != '']

    logging.info("Finished processing player list. %d players found.", len(jogadores_com_nivel))
    return jogadores_com_nivel

def sortear_times(jogadores, num_times=3):
    """Create balanced teams using CP-SAT exact optimization.

    Keeps printed output identical, logs team averages and player levels per team,
    and injects randomness to avoid input-order bias.
    """
    logging.info("Starting optimal team balancing with %d players.", len(jogadores))

    total_jogadores = len(jogadores)
    if total_jogadores < 15:
        logging.error("Insufficient players. Found %d, minimum required is 15.", total_jogadores)
        return

    base_capacity = total_jogadores // num_times
    extras = total_jogadores % num_times
    capacities = [base_capacity] * num_times
    for i in range(extras):
        capacities[i] += 1
    idx = list(range(num_times))
    random.shuffle(idx)
    capacities = [capacities[i] for i in idx]

    jogadores_shuffled = jogadores[:]
    random.shuffle(jogadores_shuffled)
    player_names = [j[0] for j in jogadores_shuffled]
    player_levels = [int(j[1]) for j in jogadores_shuffled]
    player_types = [j[2] for j in jogadores_shuffled]

    model = pulp.LpProblem("Team_Sorting", pulp.LpMinimize)

    x = pulp.LpVariable.dicts("x", ((i, t) for i in range(total_jogadores) for t in range(num_times)), cat='Binary')

    for i in range(total_jogadores):
        model += pulp.lpSum([x[(i, t)] for t in range(num_times)]) == 1

    for t in range(num_times):
        model += pulp.lpSum([x[(i, t)] for i in range(total_jogadores)]) == capacities[t]

    team_sums = [pulp.lpSum([player_levels[i] * x[(i, t)] for i in range(total_jogadores)]) for t in range(num_times)]

    z = pulp.LpVariable("z", lowBound=0, upBound=sum(player_levels), cat='Continuous')
    for t1 in range(num_times):
        for t2 in range(num_times):
            model += team_sums[t1] - team_sums[t2] <= z

    total_level_sum = sum(player_levels)
    target_sum_floor = total_level_sum // num_times
    dist_vars = []
    for t in range(num_times):
        dist_pos = pulp.LpVariable(f"dist_pos_{t}", lowBound=0, upBound=total_level_sum, cat='Continuous')
        dist_neg = pulp.LpVariable(f"dist_neg_{t}", lowBound=0, upBound=total_level_sum, cat='Continuous')
        dist_abs = pulp.LpVariable(f"dist_abs_{t}", lowBound=0, upBound=total_level_sum, cat='Continuous')
        model += team_sums[t] - target_sum_floor == dist_pos - dist_neg
        model += dist_abs == dist_pos + dist_neg
        dist_vars.append(dist_abs)

    # Soft per-level distribution penalty
    level_to_indices = {}
    for i, lvl in enumerate(player_levels):
        level_to_indices.setdefault(lvl, []).append(i)

    level_dev_vars = []
    for lvl, indices in level_to_indices.items():
        total_lvl = len(indices)
        target_per_team = total_lvl / num_times
        for t in range(num_times):
            cnt_t_lvl = pulp.lpSum([x[(i, t)] for i in indices])
            dev_pos = pulp.LpVariable(f"lvl{lvl}_dev_pos_t{t}", lowBound=0, upBound=total_lvl, cat='Continuous')
            dev_neg = pulp.LpVariable(f"lvl{lvl}_dev_neg_t{t}", lowBound=0, upBound=total_lvl, cat='Continuous')
            dev_abs = pulp.LpVariable(f"lvl{lvl}_dev_abs_t{t}", lowBound=0, upBound=total_lvl, cat='Continuous')
            # cnt_t_lvl - target == dev_pos - dev_neg
            model += cnt_t_lvl - target_per_team == dev_pos - dev_neg
            model += dev_abs == dev_pos + dev_neg
            level_dev_vars.append(dev_abs)

    BIG_W = 1000
    alpha_level_dist, level_counts = _compute_alpha_level_dist(player_levels, num_times, total_jogadores)
    logging.info("Alpha for level distribution penalty: %.2f | counts per level: %s", alpha_level_dist, level_counts)
    model += BIG_W * z + pulp.lpSum(dist_vars) + alpha_level_dist * pulp.lpSum(level_dev_vars)

    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=5, threads=8)

    status = solver.solve(model)
    if status != pulp.LpStatusOptimal and status != pulp.LpStatusFeasible:
        logging.error("Solver did not find a solution.")
        return

    teams = [[] for _ in range(num_times)]
    for i in range(total_jogadores):
        for t in range(num_times):
            if pulp.value(x[(i, t)]) == 1:
                teams[t].append((player_names[i], player_levels[i], player_types[i]))
                break

    for t in range(num_times):
        random.shuffle(teams[t])

    logging.info("Computed team averages:")
    for i, team in enumerate(teams):
        if len(team) > 0:
            total_sum = sum(p[1] for p in team)
            avg = total_sum / len(team)
            logging.info("Team %d: %.2f (Total: %d, Players: %d)", i + 1, avg, total_sum, len(team))
            for name, level, ptype in team:
                logging.info("Team %d player: %s | Level: %d | Type: %s", i + 1, name, level, ptype)
        else:
            logging.info("Team %d: No players", i + 1)

    print("*🐍⚽ Kobra FC | Sorteio dos Times*")
    print("_🤖 Sorteio Automatizado por Bot, com nivelamento para equilibrar os times. 🤖_\n")
    cores = ["🔴 *Time Vermelho*", "⚪ *Time Branco*", "🐍⚫🔵 *Time Kobra/Preto/Azul*"]

    for i, team in enumerate(teams):
        print(f"{cores[i]}")
        for j, (jogador, nivel, tipo) in enumerate(team):
            status_emoji = "✅" if tipo == "Mensalista" else "✖️"
            print(f"{j + 1}. {jogador} {status_emoji}")
        print()

    print("\n_ℹ Após realizar o pagamento, copie a mensagem acima e coloque um ✅ a frente do seu nome. Os times que pagarem primeiro começam jogando._\n")
    print("*FAVOR PAGAR ANTES DO INÍCIO DA PELADA!*\n")
    print("_Mensalista: 💵 R$ 80,00 na primeira pelada do mês_")
    print("_Avulso: 💵 R$ 23,00_")
    print("_Pix: 12685405607_")

# Chamada da função com o nome do arquivo
jogadores = processar_lista_jogadores('lista.txt')
print(jogadores)
if len(jogadores) >= 15:
    sortear_times(jogadores)
else:
    logging.error(f"Jogadores insuficientes. Apenas {len(jogadores)} encontrados. Mínimo de 15 jogadores necessário.")