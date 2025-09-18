import random
import pytest

from team_sorter import generate_balanced_teams


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
    sizes, sums, level_counts = summarize(teams)
    # Capacity check
    base = total_players // 3
    remainder = total_players % 3
    capacities = [base + (1 if i < remainder else 0) for i in range(3)]
    assert sorted(sizes) == sorted(capacities)
    # Sums should be close (spread <= 2 recommended given levels 1..4)
    assert max(sums) - min(sums) <= 3
    # Per-level distribution should differ by at most 1 across teams
    for lvl in [1, 2, 3, 4]:
        counts = [c[lvl] for c in level_counts]
        assert max(counts) - min(counts) <= 1


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


