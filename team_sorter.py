import gspread
from google.oauth2.service_account import Credentials
import re
import logging
import random
import pulp
import sys

# Configure UTF-8 encoding for console output (fixes emoji display on Windows)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Configuração do logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

data = []  # Populated only when running as a script; keeps imports safe for tests
sheet = None

# Define the expected headers based on the unique columns you need
expected_headers = [
    'ID', 'Nome do Jogador', 'Apelido', 'Tipo de Jogador', 'Nível',
    'Nível backup', '4 Níveis', 'MODA FORMS', 'MEDIANA FORMS',
    'MEDIA FORMS', 'NOVO FORMS MODA', 'Mensalista', 'Status', 'Fila Mensal'
]

if sheet is not None:
    # Obter todos os dados da aba com expected_headers
    data = sheet.get_all_records(expected_headers=expected_headers)
    logging.debug("Dados da planilha carregados com sucesso com expected_headers.")


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

def _compute_team_capacities(total_players, num_teams):
    """Compute per-team capacities as equal as possible.

    Example (3 teams):
        15 -> [5,5,5]
        16 -> [6,5,5]
        17 -> [6,6,5]
        18 -> [6,6,6]
    """
    base = total_players // num_teams
    remainder = total_players % num_teams
    capacities = [base + (1 if i < remainder else 0) for i in range(num_teams)]
    return capacities


def _build_balanced_teams(players, num_teams=3):
    """Build optimally balanced teams using Integer Linear Programming.

    Players are tuples: (name, level:int [1(best)-4(worst)], type)
    Returns a list of teams (each a list of players).
    
    This uses ILP to guarantee mathematically optimal team balance by minimizing
    the spread (difference between highest and lowest team totals).
    """
    total_players = len(players)
    capacities = _compute_team_capacities(total_players, num_teams)
    
    # Create the optimization problem
    prob = pulp.LpProblem("TeamBalancing", pulp.LpMinimize)
    
    # Decision variables: x[i][t] = 1 if player i is assigned to team t
    player_indices = range(len(players))
    team_indices = range(num_teams)
    
    x = pulp.LpVariable.dicts(
        "assign",
        ((i, t) for i in player_indices for t in team_indices),
        cat='Binary'
    )
    
    # Auxiliary variables for team sums
    team_sums = pulp.LpVariable.dicts(
        "team_sum",
        team_indices,
        lowBound=0,
        cat='Continuous'
    )
    
    # Variables for max and min team sums
    max_sum = pulp.LpVariable("max_sum", lowBound=0, cat='Continuous')
    min_sum = pulp.LpVariable("min_sum", lowBound=0, cat='Continuous')
    
    # Objective: minimize the spread (max_sum - min_sum)
    prob += max_sum - min_sum, "Minimize_Spread"
    
    # Constraint 1: Each player assigned to exactly one team
    for i in player_indices:
        prob += pulp.lpSum([x[(i, t)] for t in team_indices]) == 1, f"Player_{i}_Assignment"
    
    # Constraint 2: Team size constraints (respect capacities)
    for t in team_indices:
        prob += pulp.lpSum([x[(i, t)] for i in player_indices]) == capacities[t], f"Team_{t}_Size"
    
    # Constraint 3: Define team sums based on player levels
    for t in team_indices:
        prob += (
            team_sums[t] == pulp.lpSum([x[(i, t)] * players[i][1] for i in player_indices]),
            f"Team_{t}_Sum_Definition"
        )
    
    # Constraint 4: max_sum >= all team sums
    for t in team_indices:
        prob += max_sum >= team_sums[t], f"Max_Sum_Bound_{t}"
    
    # Constraint 5: min_sum <= all team sums
    for t in team_indices:
        prob += min_sum <= team_sums[t], f"Min_Sum_Bound_{t}"
    
    # Solve the problem (suppress solver output)
    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    
    # Check if solution was found
    if prob.status != pulp.LpStatusOptimal:
        logging.warning(
            "ILP solver did not find optimal solution. Status: %s. Falling back to heuristic.",
            pulp.LpStatus[prob.status]
        )
        # Fallback to simple round-robin if ILP fails
        return _build_teams_fallback(players, num_teams, capacities)
    
    # Extract the solution
    teams = [[] for _ in range(num_teams)]
    for i in player_indices:
        for t in team_indices:
            if pulp.value(x[(i, t)]) == 1:
                teams[t].append(players[i])
                break
    
    logging.debug(
        "ILP solution found with spread: %.2f (max: %.2f, min: %.2f)",
        pulp.value(max_sum - min_sum),
        pulp.value(max_sum),
        pulp.value(min_sum)
    )
    
    return teams


def _build_teams_fallback(players, num_teams, capacities):
    """Simple fallback method if ILP fails (should rarely happen).
    
    Uses a round-robin assignment sorted by level to ensure basic balance.
    """
    logging.warning("Using fallback team assignment method")
    
    # Sort players by level (best first) for better distribution
    sorted_players = sorted(players, key=lambda p: p[1])
    
    teams = [[] for _ in range(num_teams)]
    team_sizes = [0] * num_teams
    
    for player in sorted_players:
        # Find team with smallest size that hasn't reached capacity
        candidates = [t for t in range(num_teams) if team_sizes[t] < capacities[t]]
        if not candidates:
            break
        
        # Assign to team with fewest players
        team_idx = min(candidates, key=lambda t: team_sizes[t])
        teams[team_idx].append(player)
        team_sizes[team_idx] += 1
    
    return teams


def generate_balanced_teams(jogadores, num_times=3):
    """Public helper to generate teams for testing/use without printing/logging."""
    return _build_balanced_teams(jogadores, num_times)


def sortear_times(jogadores, num_times=3):
    """Create balanced teams with capacity constraints and post-processing.
    Keeps existing logs and output formatting.
    """
    logging.info("Starting team balancing with %d players.", len(jogadores))
    
    total_jogadores = len(jogadores)
    if total_jogadores < 15:
        logging.error("Insufficient players. Found %d, minimum required is 15.", total_jogadores)
        return
    if total_jogadores > 18:
        logging.error("Too many players. Found %d, maximum allowed is 18.", total_jogadores)
        return
    
    teams = _build_balanced_teams(jogadores, num_times)
    
    # Shuffle team order and players within teams for randomness (presentation only)
    random.shuffle(teams)
    for team in teams:
        random.shuffle(team)
    
    # Log team statistics (unchanged)
    logging.info("Team balancing completed.")
    logging.info("Team composition:")
    for i, team in enumerate(teams):
        if len(team) > 0:
            total_sum = sum(p[1] for p in team)
            avg = total_sum / len(team)
            logging.info("Team %d: %.2f (Total: %d, Players: %d)", i + 1, avg, total_sum, len(team))
            for name, level, ptype in team:
                logging.info("Team %d player: %s | Level: %d | Type: %s", i + 1, name, level, ptype)
        else:
            logging.info("Team %d: No players", i + 1)
    
    # Print formatted output (unchanged)
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

if __name__ == '__main__':
    # Autenticação com a conta de serviço e leitura da planilha
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    creds = Credentials.from_service_account_file('lofty-mark-407721-c78e211dcebf.json', scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key('1GRC7L_2Q75OEywLz-gFU6jrcKLhpgA3-1LZ_qFviFsY')
    sheet = spreadsheet.get_worksheet(2)  # A terceira aba
    data = sheet.get_all_records(expected_headers=expected_headers)
    logging.debug("Dados da planilha carregados com sucesso com expected_headers.")

    # Chamada da função com o nome do arquivo
    jogadores = processar_lista_jogadores('lista.txt')
    print(jogadores)
    if 15 <= len(jogadores) <= 18:
        sortear_times(jogadores)
    elif len(jogadores) < 15:
        logging.error(f"Jogadores insuficientes. Apenas {len(jogadores)} encontrados. Mínimo de 15 jogadores necessário.")
    else:
        logging.error("Too many players for a 3-team draw. Found %d (max 18).", len(jogadores))