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

    linhas = re.findall(r"\d{2}\.\s+(.+)", lista_input)
    logging.debug("%d players extracted from list: %s", len(linhas), linhas)

    jogadores = []
    fixos = {}
    for linha in linhas:
        apelido, time = extrair_cor_fixa(linha)
        jogadores.append(apelido)
        if time is not None:
            fixos[apelido] = time
    if fixos:
        logging.info("Fixed team assignments: %s", fixos)

    jogadores_com_nivel = [(apelido, *buscar_nivel_e_tipo(apelido)) for apelido in jogadores]
    jogadores_com_nivel = [j for j in jogadores_com_nivel if j[1] is not None and j[1] != '']

    logging.info("Finished processing player list. %d players found.", len(jogadores_com_nivel))
    return jogadores_com_nivel, fixos


# Cores dos times, na ordem dos índices usados pelo sorteio
CORES = ["🔴 *Time Vermelho*", "⚪ *Time Branco*", "🐍⚫🔵 *Time Kobra/Preto/Azul*"]

# Nomes aceitos (minúsculos) para fixar um jogador em uma cor -> índice do time
ALIASES_CORES = {
    'vermelho': 0, 'red': 0, 'v': 0,
    'branco': 1, 'white': 1, 'b': 1,
    'preto': 2, 'azul': 2, 'kobra': 2, 'p': 2, 'k': 2,
}


def extrair_cor_fixa(linha):
    """Split a list line into (apelido, team_index or None).

    A player can be forced into a color with a trailing tag in brackets,
    e.g. "Egeu [Vermelho]", "Vini [branco]", "Tene [Preto]".
    """
    match = re.match(r"^(.*?)\s*\[([^\]]+)\]\s*$", linha.strip())
    if not match:
        return linha.strip(), None
    apelido, cor = match.group(1).strip(), match.group(2).strip().lower()
    if cor not in ALIASES_CORES:
        logging.error("Unknown color '%s' for player %s. Ignoring fixed color.", cor, apelido)
        return apelido, None
    return apelido, ALIASES_CORES[cor]

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


def _validate_fixed(players, fixed, num_teams):
    """Raise ValueError if fixed assignments are impossible to satisfy."""
    names = {p[0] for p in players}
    unknown = [name for name in fixed if name not in names]
    if unknown:
        raise ValueError(f"Fixed players not in the player list: {unknown}")
    bad_teams = [name for name, t in fixed.items() if not 0 <= t < num_teams]
    if bad_teams:
        raise ValueError(f"Invalid team index for fixed players: {bad_teams}")
    max_capacity = max(_compute_team_capacities(len(players), num_teams))
    for t in range(num_teams):
        count = sum(1 for team in fixed.values() if team == t)
        if count > max_capacity:
            raise ValueError(
                f"Team {t} has {count} fixed players, but max capacity is {max_capacity}"
            )


def _build_balanced_teams(players, num_teams=3, fixed=None):
    """Build optimally balanced teams using Integer Linear Programming.

    Players are tuples: (name, level:int [1(best)-4(worst)], type)
    `fixed` optionally maps player name -> team index the player must be in.
    Returns a list of teams (each a list of players), where index t is team/color t.

    This uses ILP to guarantee mathematically optimal team balance by minimizing
    the spread (difference between highest and lowest team totals).
    Randomizes player order before optimization to ensure variety between runs.
    """
    fixed = fixed or {}
    _validate_fixed(players, fixed, num_teams)

    # Shuffle players to introduce randomness (especially for same-level players)
    # This ensures different but equally optimal solutions on each run
    players = players.copy()  # Don't modify the original list
    random.shuffle(players)

    total_players = len(players)
    min_size = total_players // num_teams
    max_size = min_size + (1 if total_players % num_teams else 0)

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
    
    # Constraint 2: Team sizes as equal as possible. The solver picks which teams
    # get the extra player(s), so fixed players never make the problem infeasible.
    for t in team_indices:
        team_size = pulp.lpSum([x[(i, t)] for i in player_indices])
        prob += team_size >= min_size, f"Team_{t}_Min_Size"
        prob += team_size <= max_size, f"Team_{t}_Max_Size"

    # Constraint 2b: Players fixed to a specific team/color
    for i in player_indices:
        name = players[i][0]
        if name in fixed:
            prob += x[(i, fixed[name])] == 1, f"Player_{i}_Fixed"

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
        return _build_teams_fallback(players, num_teams, fixed)

    # Extract the solution
    teams = [[] for _ in range(num_teams)]
    for i in player_indices:
        for t in team_indices:
            if round(pulp.value(x[(i, t)])) == 1:
                teams[t].append(players[i])
                break
    
    logging.debug(
        "ILP solution found with spread: %.2f (max: %.2f, min: %.2f)",
        pulp.value(max_sum - min_sum),
        pulp.value(max_sum),
        pulp.value(min_sum)
    )
    
    return teams


def _build_teams_fallback(players, num_teams, fixed=None):
    """Simple fallback method if ILP fails (should rarely happen).

    Places fixed players first, then uses a round-robin assignment sorted by
    level to ensure basic balance.
    """
    logging.warning("Using fallback team assignment method")
    fixed = fixed or {}

    teams = [[] for _ in range(num_teams)]
    team_sizes = [0] * num_teams

    # Give the extra slots to the teams with the most fixed players
    fixed_counts = [sum(1 for t in fixed.values() if t == team) for team in range(num_teams)]
    order = sorted(range(num_teams), key=lambda t: -fixed_counts[t])
    sizes = _compute_team_capacities(len(players), num_teams)
    capacities = [0] * num_teams
    for team, size in zip(order, sizes):
        capacities[team] = size

    for player in players:
        if player[0] in fixed:
            team_idx = fixed[player[0]]
            teams[team_idx].append(player)
            team_sizes[team_idx] += 1

    # Sort players by level (best first) for better distribution
    sorted_players = sorted((p for p in players if p[0] not in fixed), key=lambda p: p[1])

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


def generate_balanced_teams(jogadores, num_times=3, fixos=None):
    """Public helper to generate teams for testing/use without printing/logging.

    `fixos` optionally maps player name -> team index (0=Vermelho, 1=Branco, 2=Kobra/Preto/Azul).
    """
    return _build_balanced_teams(jogadores, num_times, fixos)


def sortear_times(jogadores, num_times=3, fixos=None):
    """Create balanced teams with capacity constraints and post-processing.
    Keeps existing logs and output formatting.
    `fixos` optionally maps player name -> team index (see CORES).
    """
    logging.info("Starting team balancing with %d players.", len(jogadores))
    
    total_jogadores = len(jogadores)
    if total_jogadores < 15:
        logging.error("Insufficient players. Found %d, minimum required is 15.", total_jogadores)
        return
    if total_jogadores > 18:
        logging.error("Too many players. Found %d, maximum allowed is 18.", total_jogadores)
        return
    
    try:
        teams = _build_balanced_teams(jogadores, num_times, fixos)
    except ValueError as e:
        logging.error("Invalid fixed colors: %s", e)
        return

    # Shuffle team colors (unless some are fixed) and players within teams (presentation only)
    if not fixos:
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
    for i, team in enumerate(teams):
        print(f"{CORES[i]}")
        for j, (jogador, nivel, tipo) in enumerate(team):
            status_emoji = "✅" if tipo == "Mensalista" else "✖️"
            print(f"{j + 1}. {jogador} {status_emoji}")
        print()
    
    print("\n_ℹ Após realizar o pagamento, copie a mensagem acima e coloque um ✅ a frente do seu nome. Os times que pagarem primeiro começam jogando._\n")
    print("*FAVOR PAGAR ANTES DO INÍCIO DA PELADA!*\n")
    print("_Mensalista: 💵 R$ 69,00 na primeira pelada do mês_")
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
    jogadores, fixos = processar_lista_jogadores('lista.txt')
    print(jogadores)
    if 15 <= len(jogadores) <= 18:
        sortear_times(jogadores, fixos=fixos)
    elif len(jogadores) < 15:
        logging.error(f"Jogadores insuficientes. Apenas {len(jogadores)} encontrados. Mínimo de 15 jogadores necessário.")
    else:
        logging.error("Too many players for a 3-team draw. Found %d (max 18).", len(jogadores))