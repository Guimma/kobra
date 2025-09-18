import gspread
from google.oauth2.service_account import Credentials
import re
import logging
import random
import pulp

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
    """Build teams with capacity constraints and per-level distribution.

    Players are tuples: (name, level:int [1(best)-4(worst)], type)
    Returns a list of teams (each a list of players), without printing or logging.
    """
    total_players = len(players)
    capacities = _compute_team_capacities(total_players, num_teams)

    teams = [[] for _ in range(num_teams)]
    team_sizes = [0] * num_teams
    team_sums = [0] * num_teams
    per_level_counts = [{1: 0, 2: 0, 3: 0, 4: 0} for _ in range(num_teams)]

    # Group players by level and shuffle within each bucket for randomness
    by_level = {1: [], 2: [], 3: [], 4: []}
    for p in players:
        name, level, ptype = p
        if level in by_level:
            by_level[level].append(p)
    for lvl in by_level:
        random.shuffle(by_level[lvl])

    # Target per-level distribution bounds for swap constraints
    per_level_totals = {lvl: len(by_level[lvl]) for lvl in by_level}
    per_level_low = {lvl: per_level_totals[lvl] // num_teams for lvl in by_level}
    per_level_high = {
        lvl: per_level_low[lvl] + (1 if (per_level_totals[lvl] % num_teams) > 0 else 0)
        for lvl in by_level
    }

    def choose_team_for_level(level):
        # Primary: fewer players of this level
        # Secondary: fewer total players (respect capacities)
        # Tertiary: balance sums (for strong players prefer higher sum; for weak prefer lower sum)
        sum_key_sign = -1 if level in (1, 2) else 1
        candidates = [i for i in range(num_teams) if team_sizes[i] < capacities[i]]
        # Random component to avoid systemic bias
        rands = {i: random.random() for i in candidates}
        def key(i):
            return (
                per_level_counts[i][level],
                team_sizes[i],
                sum_key_sign * team_sums[i],
                rands[i]
            )
        candidates.sort(key=key)
        return candidates[0]

    # Distribute by levels in ascending strength order ensures bucket fairness first
    for level in [1, 2, 3, 4]:
        while by_level[level]:
            team_idx = choose_team_for_level(level)
            player = by_level[level].pop()
            teams[team_idx].append(player)
            team_sizes[team_idx] += 1
            team_sums[team_idx] += player[1]
            per_level_counts[team_idx][level] += 1

    # Local swap post-processing to reduce spread of sums while respecting per-level bounds
    def within_level_bounds(team_idx, level, delta):
        new_count = per_level_counts[team_idx][level] + delta
        return per_level_low[level] <= new_count <= per_level_high[level]

    def try_improve_with_swaps(max_iterations=100):
        for _ in range(max_iterations):
            improved = False
            pre_spread = max(team_sums) - min(team_sums)
            for i in range(num_teams):
                for j in range(i + 1, num_teams):
                    for a_idx, a in enumerate(teams[i]):
                        for b_idx, b in enumerate(teams[j]):
                            if a[1] == b[1]:
                                continue  # swapping equal levels does not change sums
                            # Check per-level bounds if swapped
                            ai, bi = a[1], b[1]
                            if not (within_level_bounds(i, ai, -1) and within_level_bounds(j, ai, +1)):
                                continue
                            if not (within_level_bounds(j, bi, -1) and within_level_bounds(i, bi, +1)):
                                continue
                            new_si = team_sums[i] - ai + bi
                            new_sj = team_sums[j] - bi + ai
                            new_sums = team_sums[:]
                            new_sums[i] = new_si
                            new_sums[j] = new_sj
                            new_spread = max(new_sums) - min(new_sums)
                            if new_spread < pre_spread:
                                # Apply swap
                                teams[i][a_idx], teams[j][b_idx] = teams[j][b_idx], teams[i][a_idx]
                                team_sums[i], team_sums[j] = new_si, new_sj
                                per_level_counts[i][ai] -= 1
                                per_level_counts[i][bi] += 1
                                per_level_counts[j][bi] -= 1
                                per_level_counts[j][ai] += 1
                                improved = True
                                break
                        if improved:
                            break
                    if improved:
                        break
            if not improved:
                return

    try_improve_with_swaps()
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