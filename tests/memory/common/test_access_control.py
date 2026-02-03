"""Tests for access control logic."""

from memory.common.access_control import (
    SensitivityLevel,
    ProjectRole,
    ROLE_SENSITIVITY,
    AccessCondition,
    AccessFilter,
    has_admin_scope,
    user_can_access,
    user_can_create_in_project,
    build_access_filter,
    get_allowed_project_ids,
    get_max_sensitivity_for_project,
)


class MockUser:
    """Mock user for testing."""

    id: int | None
    scopes: list[str]

    def __init__(self, id: int | None = 1, scopes: list[str] | None = None):
        self.id = id
        self.scopes = scopes or []


class MockSourceItem:
    """Mock source item for testing."""

    def __init__(self, project_id: int | None, sensitivity: str | None):
        self.project_id = project_id
        self.sensitivity = sensitivity


# --- Role and Sensitivity Tests ---


def test_role_sensitivity_contributor():
    """Contributors can see public and basic content."""
    assert ROLE_SENSITIVITY[ProjectRole.CONTRIBUTOR] == frozenset({
        SensitivityLevel.PUBLIC,
        SensitivityLevel.BASIC,
    })


def test_role_sensitivity_manager():
    """Managers can see public, basic, and internal content."""
    assert ROLE_SENSITIVITY[ProjectRole.MANAGER] == frozenset({
        SensitivityLevel.PUBLIC,
        SensitivityLevel.BASIC,
        SensitivityLevel.INTERNAL,
    })


def test_role_sensitivity_admin():
    """Admins can see all sensitivity levels."""
    assert ROLE_SENSITIVITY[ProjectRole.ADMIN] == frozenset({
        SensitivityLevel.PUBLIC,
        SensitivityLevel.BASIC,
        SensitivityLevel.INTERNAL,
        SensitivityLevel.CONFIDENTIAL,
    })


# --- Access Condition Tests ---


def test_access_condition_frozen():
    """AccessCondition should be immutable."""
    condition = AccessCondition(project_id=1, sensitivities=frozenset({"basic"}))
    assert condition.project_id == 1
    assert condition.sensitivities == frozenset({"basic"})


# --- Access Filter Tests ---


def test_access_filter_is_empty_no_conditions():
    """Empty filter should report is_empty=True."""
    filter = AccessFilter(conditions=[])
    assert filter.is_empty() is True


def test_access_filter_is_empty_with_conditions():
    """Filter with conditions should report is_empty=False."""
    condition = AccessCondition(project_id=1, sensitivities=frozenset({"basic"}))
    filter = AccessFilter(conditions=[condition])
    assert filter.is_empty() is False


# --- has_admin_scope Tests ---


def test_has_admin_scope_with_star():
    """User with '*' scope is superadmin."""
    user = MockUser(scopes=["*"])
    assert has_admin_scope(user) is True


def test_has_admin_scope_with_admin():
    """User with 'admin' scope is superadmin."""
    user = MockUser(scopes=["admin"])
    assert has_admin_scope(user) is True


def test_has_admin_scope_regular_user():
    """User without admin scope is not superadmin."""
    user = MockUser(scopes=["read", "write"])
    assert has_admin_scope(user) is False


def test_has_admin_scope_empty_scopes():
    """User with empty scopes is not superadmin."""
    user = MockUser(scopes=[])
    assert has_admin_scope(user) is False


def test_has_admin_scope_none_scopes():
    """User with None scopes is not superadmin."""
    user = MockUser(scopes=None)
    assert has_admin_scope(user) is False


# --- user_can_access Tests ---


def test_user_can_access_superadmin():
    """Superadmins can access any content."""
    user = MockUser(scopes=["admin"])
    item = MockSourceItem(project_id=None, sensitivity="confidential")
    assert user_can_access(user, item) is True


def test_user_can_access_null_project_denied():
    """Regular users cannot access content with NULL project_id."""
    user = MockUser(scopes=[])
    project_roles = {1: "admin"}
    item = MockSourceItem(project_id=None, sensitivity="basic")
    assert user_can_access(user, item, project_roles) is False


def test_user_can_access_contributor_basic():
    """Contributors can access basic content in their project."""
    user = MockUser(scopes=[])
    project_roles = {1: "contributor"}
    item = MockSourceItem(project_id=1, sensitivity="basic")
    assert user_can_access(user, item, project_roles) is True


def test_user_can_access_contributor_internal_denied():
    """Contributors cannot access internal content."""
    user = MockUser(scopes=[])
    project_roles = {1: "contributor"}
    item = MockSourceItem(project_id=1, sensitivity="internal")
    assert user_can_access(user, item, project_roles) is False


def test_user_can_access_contributor_confidential_denied():
    """Contributors cannot access confidential content."""
    user = MockUser(scopes=[])
    project_roles = {1: "contributor"}
    item = MockSourceItem(project_id=1, sensitivity="confidential")
    assert user_can_access(user, item, project_roles) is False


def test_user_can_access_manager_basic():
    """Managers can access basic content."""
    user = MockUser(scopes=[])
    project_roles = {1: "manager"}
    item = MockSourceItem(project_id=1, sensitivity="basic")
    assert user_can_access(user, item, project_roles) is True


def test_user_can_access_manager_internal():
    """Managers can access internal content."""
    user = MockUser(scopes=[])
    project_roles = {1: "manager"}
    item = MockSourceItem(project_id=1, sensitivity="internal")
    assert user_can_access(user, item, project_roles) is True


def test_user_can_access_manager_confidential_denied():
    """Managers cannot access confidential content."""
    user = MockUser(scopes=[])
    project_roles = {1: "manager"}
    item = MockSourceItem(project_id=1, sensitivity="confidential")
    assert user_can_access(user, item, project_roles) is False


def test_user_can_access_admin_all_levels():
    """Admins can access all sensitivity levels."""
    user = MockUser(scopes=[])
    project_roles = {1: "admin"}

    for sensitivity in ["basic", "internal", "confidential"]:
        item = MockSourceItem(project_id=1, sensitivity=sensitivity)
        assert user_can_access(user, item, project_roles) is True


def test_user_can_access_wrong_project_denied():
    """Users cannot access content from projects they're not in."""
    user = MockUser(scopes=[])
    project_roles = {1: "admin"}
    item = MockSourceItem(project_id=2, sensitivity="basic")
    assert user_can_access(user, item, project_roles) is False


def test_user_can_access_no_project_roles_denied():
    """Users with no project roles cannot access anything."""
    user = MockUser(scopes=[])
    item = MockSourceItem(project_id=1, sensitivity="basic")
    assert user_can_access(user, item, {}) is False


def test_user_can_access_none_project_roles_denied():
    """Users with None project roles cannot access anything."""
    user = MockUser(scopes=[])
    item = MockSourceItem(project_id=1, sensitivity="basic")
    assert user_can_access(user, item, None) is False


def test_user_can_access_multiple_projects():
    """Users with multiple project roles can access content from any of their projects."""
    user = MockUser(scopes=[])
    project_roles = {1: "contributor", 2: "admin"}

    # Can access basic in project 1
    item1 = MockSourceItem(project_id=1, sensitivity="basic")
    assert user_can_access(user, item1, project_roles) is True

    # Can access confidential in project 2
    item2 = MockSourceItem(project_id=2, sensitivity="confidential")
    assert user_can_access(user, item2, project_roles) is True

    # Cannot access internal in project 1 (only contributor)
    item3 = MockSourceItem(project_id=1, sensitivity="internal")
    assert user_can_access(user, item3, project_roles) is False


# --- user_can_create_in_project Tests ---


def test_user_can_create_superadmin():
    """Superadmins can create content at any sensitivity level."""
    user = MockUser(scopes=["admin"])

    for sensitivity in ["basic", "internal", "confidential"]:
        assert user_can_create_in_project(user, 1, sensitivity) is True


def test_user_can_create_contributor_basic():
    """Contributors can create basic content."""
    user = MockUser(scopes=[])
    project_roles = {1: "contributor"}

    assert user_can_create_in_project(user, 1, "basic", project_roles) is True


def test_user_can_create_contributor_internal_denied():
    """Contributors cannot create internal content."""
    user = MockUser(scopes=[])
    project_roles = {1: "contributor"}

    assert user_can_create_in_project(user, 1, "internal", project_roles) is False


def test_user_can_create_with_sensitivity_enum():
    """user_can_create_in_project works with SensitivityLevel enum."""
    user = MockUser(scopes=[])
    project_roles = {1: "admin"}

    assert user_can_create_in_project(
        user, 1, SensitivityLevel.CONFIDENTIAL, project_roles
    ) is True


def test_user_can_create_not_in_project_denied():
    """Users cannot create content in projects they're not in."""
    user = MockUser(scopes=[])
    project_roles = {1: "admin"}

    assert user_can_create_in_project(user, 2, "basic", project_roles) is False


def test_user_can_create_none_project_roles_denied():
    """Users with None project roles cannot create content."""
    user = MockUser(scopes=[])

    assert user_can_create_in_project(user, 1, "basic", None) is False


# --- build_access_filter Tests ---


def test_build_access_filter_superadmin_returns_none():
    """Superadmins get no filter (None)."""
    user = MockUser(scopes=["admin"])
    result = build_access_filter(user, {})
    assert result is None


def test_build_access_filter_no_project_roles_empty():
    """Users with no project roles get empty filter."""
    user = MockUser(scopes=[])
    result = build_access_filter(user, {})
    assert result is not None
    assert result.is_empty() is True


def test_build_access_filter_none_project_roles_empty():
    """Users with None project roles get empty filter."""
    user = MockUser(scopes=[])
    result = build_access_filter(user, None)
    assert result is not None
    assert result.is_empty() is True


def test_build_access_filter_single_project():
    """Filter includes correct conditions for single project role."""
    user = MockUser(scopes=[])
    project_roles = {1: "manager"}
    result = build_access_filter(user, project_roles)

    assert result is not None
    assert len(result.conditions) == 1
    condition = result.conditions[0]
    assert condition.project_id == 1
    assert condition.sensitivities == frozenset({"public", "basic", "internal"})


def test_build_access_filter_multiple_projects():
    """Filter includes conditions for all project roles."""
    user = MockUser(scopes=[])
    project_roles = {1: "contributor", 2: "admin"}
    result = build_access_filter(user, project_roles)

    assert result is not None
    assert len(result.conditions) == 2

    # Find conditions by project_id
    cond_by_project = {c.project_id: c for c in result.conditions}

    assert cond_by_project[1].sensitivities == frozenset({"public", "basic"})
    assert cond_by_project[2].sensitivities == frozenset({"public", "basic", "internal", "confidential"})


def test_build_access_filter_invalid_role_skipped():
    """Invalid roles are skipped, not included in filter."""
    user = MockUser(scopes=[])
    project_roles = {1: "invalid_role", 2: "admin"}
    result = build_access_filter(user, project_roles)

    assert result is not None
    assert len(result.conditions) == 1
    assert result.conditions[0].project_id == 2


# --- get_allowed_project_ids Tests ---


def test_get_allowed_project_ids_empty():
    """No project roles returns empty set."""
    assert get_allowed_project_ids({}) == set()


def test_get_allowed_project_ids_single():
    """Single project role returns that project ID."""
    project_roles = {1: "contributor"}
    assert get_allowed_project_ids(project_roles) == {1}


def test_get_allowed_project_ids_multiple():
    """Multiple project roles returns all project IDs."""
    project_roles = {1: "contributor", 2: "admin", 3: "manager"}
    assert get_allowed_project_ids(project_roles) == {1, 2, 3}


# --- get_max_sensitivity_for_project Tests ---


def test_get_max_sensitivity_not_in_project():
    """Returns None if user not in project."""
    project_roles = {1: "admin"}
    result = get_max_sensitivity_for_project(project_roles, project_id=2)
    assert result is None


def test_get_max_sensitivity_contributor():
    """Contributor max sensitivity is basic."""
    project_roles = {1: "contributor"}
    result = get_max_sensitivity_for_project(project_roles, project_id=1)
    assert result == SensitivityLevel.BASIC


def test_get_max_sensitivity_manager():
    """Manager max sensitivity is internal."""
    project_roles = {1: "manager"}
    result = get_max_sensitivity_for_project(project_roles, project_id=1)
    assert result == SensitivityLevel.INTERNAL


def test_get_max_sensitivity_admin():
    """Admin max sensitivity is confidential."""
    project_roles = {1: "admin"}
    result = get_max_sensitivity_for_project(project_roles, project_id=1)
    assert result == SensitivityLevel.CONFIDENTIAL


def test_get_max_sensitivity_invalid_role():
    """Invalid role returns None."""
    project_roles = {1: "invalid"}
    result = get_max_sensitivity_for_project(project_roles, project_id=1)
    assert result is None
