from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def test_run_batch_profiles_defaults_to_market_cap_v1() -> None:
    script_path = Path(__file__).parents[1] / "scripts" / "factors" / "run_batch_profiles.py"
    spec = spec_from_file_location("run_batch_profiles_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    args = module.build_parser().parse_args(
        ["--start-date", "2026-07-01", "--end-date", "2026-07-17"]
    )

    assert args.size_factor_name == "market_cap"
    assert args.size_factor_version == "v1"
