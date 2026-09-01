import pytest

from ebuild.packages.recipe import PackageRecipe
from ebuild.packages.registry import PackageRegistry
from ebuild.packages.resolver import PackageResolver, ResolveError


def make_recipe(version: str) -> PackageRecipe:
    return PackageRecipe(
        name="demo",
        version=version,
        url="https://example.com/demo.tar.gz",
    )


def registry_with(*versions: str) -> PackageRegistry:
    registry = PackageRegistry()
    for version in versions:
        registry._register(make_recipe(version))
    return registry


def test_list_all_versions_uses_numeric_version_order():
    registry = PackageRegistry()

    registry._register(make_recipe("1.2.0"))
    registry._register(make_recipe("1.10.0"))
    registry._register(make_recipe("1.9.0"))

    versions = registry.list_all_versions("demo")

    assert [recipe.version for recipe in versions] == [
        "1.2.0",
        "1.9.0",
        "1.10.0",
    ]


# ── Out-of-format versions must not crash the registry ──────
#
# The numeric ordering key introduced for list_all_versions() parsed every
# dot-separated component with int(). Versions that are not purely numeric --
# including "v2.9.3", the upstream tag form recipes/littlefs.yaml already
# downloads -- raised ValueError from get(), list_packages() and
# list_all_versions() alike.

OUT_OF_FORMAT_VERSIONS = [
    "v2.9.3",       # upstream git tag, as used by recipes/littlefs.yaml's URL
    "1.2.13-1",     # distribution revision
    "3.6.0-rc1",    # prerelease
    "1.0.0+build2",  # build metadata
]


@pytest.mark.parametrize("version", OUT_OF_FORMAT_VERSIONS)
def test_registry_lookups_do_not_crash_on_out_of_format_version(version):
    registry = PackageRegistry()
    registry._register(make_recipe(version))

    assert registry.get("demo").version == version
    assert [r.version for r in registry.list_packages()] == [version]
    assert [r.version for r in registry.list_all_versions("demo")] == [version]


def test_numeric_release_is_preferred_over_out_of_format_sibling():
    """A plain numeric release must win over a prerelease of the same number."""
    registry = PackageRegistry()
    registry._register(make_recipe("3.6.0-rc1"))
    registry._register(make_recipe("3.6.0"))

    assert registry.get("demo").version == "3.6.0"


def test_out_of_format_versions_order_deterministically():
    registry = PackageRegistry()
    for version in ("1.9.0", "v2.9.3", "1.10.0", "1.2.13-1"):
        registry._register(make_recipe(version))

    ordered = [r.version for r in registry.list_all_versions("demo")]

    # The registry orders through version_sort_key, which reads these forms
    # rather than ranking them all below the numeric ones: a leading "v" is
    # ignored (v2.9.3 is littlefs's own tag format, so it has to order as
    # 2.9.3, not below 1.10.0), and a suffix sorts just under the release it
    # qualifies, so 1.2.13-1 precedes 1.9.0. The registry previously used a
    # second, private key that ranked every non-numeric version below every
    # numeric one; the two disagreed and only the private one was wired in.
    assert ordered == ["1.2.13-1", "1.9.0", "1.10.0", "v2.9.3"]


def test_one_out_of_format_recipe_does_not_hide_valid_packages():
    registry = PackageRegistry()
    registry._register(
        PackageRecipe(
            name="littlefs",
            version="v2.9.3",
            url="https://example.com/littlefs.tar.gz",
        )
    )
    registry._register(
        PackageRecipe(
            name="zlib",
            version="1.3.1",
            url="https://example.com/zlib.tar.gz",
        )
    )

    assert sorted(r.name for r in registry.list_packages()) == ["littlefs", "zlib"]


def test_missing_package_raises_resolve_error_not_value_error():
    """The resolver's not-found message enumerates the registry.

    With an out-of-format recipe registered, building that message used to
    raise ValueError, replacing the actionable ResolveError with a traceback.
    """
    registry = PackageRegistry()
    registry._register(
        PackageRecipe(
            name="littlefs",
            version="v2.9.3",
            url="https://example.com/littlefs.tar.gz",
        )
    )
    resolver = PackageResolver(registry)

    with pytest.raises(ResolveError, match="absent_package"):
        resolver.resolve([{"name": "absent_package", "version": None}])
