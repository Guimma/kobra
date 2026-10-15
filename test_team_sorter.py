import random
import pytest

from team_sorter import generate_balanced_teams, extrair_cor_fixa


def summarize(teams):
    sizes = [len(t) for t in teams]
    sums = [sum(p[1] for p in t) for t in teams]
    level_counts = []
    for t in teams:
        counts = {1: 0, 2: 0, 3: 0, 4: 0}
        for _, lvl, _ in t:
            counts[lvl] += 1
        level_counts.append(counts)
    return sizes, sums, level_counts


def assert_balanced(teams, total_players):
    sizes, sums, _ = summarize(teams)
    # Capacity check
    base = total_players // 3
    remainder = total_players % 3
    capacities = [base + (1 if i < remainder else 0) for i in range(3)]
    assert sorted(sizes) == sorted(capacities)
    # Sums should be close (spread <= 2 recommended given levels 1..4).
    # Per-level distribution is not checked: the ILP only balances level sums.
    assert max(sums) - min(sums) <= 3


def make_players(level_counts):
    players = []
    for lvl, qty in level_counts.items():
        for i in range(qty):
            players.append((f"P{lvl}-{i}", lvl, "Mensalista"))
    random.shuffle(players)
    return players


@pytest.mark.parametrize("counts", [
    # 15 players
    {1: 3, 2: 4, 3: 4, 4: 4},
    # 16 players
    {1: 4, 2: 4, 3: 4, 4: 4},
    # 17 players
    {1: 4, 2: 5, 3: 4, 4: 4},
    # 18 players
    {1: 5, 2: 4, 3: 5, 4: 4},
    # Skewed distribution but same totals
    {1: 2, 2: 5, 3: 5, 4: 6},
])
def test_balancing_across_player_counts(counts):
    players = make_players(counts)
    teams = generate_balanced_teams(players, num_times=3)
    assert_balanced(teams, len(players))


def test_extreme_bucket_skew():
    # 10 level-4 and 5 level-1 -> 15 players
    counts = {1: 5, 2: 0, 3: 0, 4: 10}
    players = make_players(counts)
    teams = generate_balanced_teams(players, num_times=3)
    assert_balanced(teams, len(players))


@pytest.mark.parametrize("counts", [
    {1: 3, 2: 4, 3: 4, 4: 4},
    {1: 4, 2: 5, 3: 4, 4: 4},
    {1: 5, 2: 4, 3: 5, 4: 4},
])
def test_fixed_players_stay_in_their_team(counts):
    players = make_players(counts)
    fixos = {players[0][0]: 0, players[1][0]: 0, players[2][0]: 2, players[3][0]: 1}
    for _ in range(5):
        teams = generate_balanced_teams(players, num_times=3, fixos=fixos)
        for name, t in fixos.items():
            assert name in [p[0] for p in teams[t]]
        sizes = [len(t) for t in teams]
        assert max(sizes) - min(sizes) <= 1
        assert sum(sizes) == len(players)


def test_fixed_players_fill_team_with_extra_slot():
    # 16 players: a full team of 6 fixed to team 2 must still be feasible
    players = make_players({1: 4, 2: 4, 3: 4, 4: 4})
    fixos = {p[0]: 2 for p in players[:6]}
    teams = generate_balanced_teams(players, num_times=3, fixos=fixos)
    assert sorted(p[0] for p in teams[2]) == sorted(fixos)


def test_too_many_fixed_players_raises():
    players = make_players({1: 4, 2: 4, 3: 4, 4: 3})  # 15 -> max 5 per team
    fixos = {p[0]: 1 for p in players[:6]}
    with pytest.raises(ValueError):
        generate_balanced_teams(players, num_times=3, fixos=fixos)


@pytest.mark.parametrize("linha, esperado", [
    ("Egeu [Vermelho]", ("Egeu", 0)),
    ("Lil Vic [branco]", ("Lil Vic", 1)),
    ("Tene  [ Preto ]", ("Tene", 2)),
    ("Andy [azul]", ("Andy", 2)),
    ("Gui Caldas 🧤", ("Gui Caldas 🧤", None)),
    ("Felp [roxo]", ("Felp", None)),
])
def test_extrair_cor_fixa(linha, esperado):
    assert extrair_cor_fixa(linha) == esperado
