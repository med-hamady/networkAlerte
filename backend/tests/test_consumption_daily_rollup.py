

def test_catchup_path_resolves_its_setting():
    """Le chemin de rattrapage (résumé non vide) lisait `settings`, jamais défini :
    chaque nuit après la première levait une NameError, avalée par le `except`,
    et plus aucune journée n'était totalisée. ruff F821 l'attrape — ce test aussi."""
    import ast
    import inspect
    import textwrap

    from app.tasks import jobs

    tree = ast.parse(textwrap.dedent(inspect.getsource(jobs.client_consumption_daily_rollup_job)))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "settings" not in names or hasattr(jobs, "settings")
