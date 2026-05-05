"""Shared helpers for searchable/sortable list pages."""

PER_PAGE_OPTIONS = (10, 25, 50, 100)


def parse_list_controls(
    args,
    allowed_sorts,
    default_sort,
    default_direction='asc',
    default_per_page=25,
):
    """Validate common list query-string controls."""
    page = args.get('page', 1, type=int) or 1
    if page < 1:
        page = 1

    per_page = args.get('per_page', default_per_page, type=int) or default_per_page
    if per_page not in PER_PAGE_OPTIONS:
        per_page = default_per_page

    sort = (args.get('sort') or default_sort).strip()
    if sort not in allowed_sorts:
        sort = default_sort

    direction = (args.get('direction') or default_direction).strip().lower()
    if direction not in ('asc', 'desc'):
        direction = default_direction

    return {
        'page': page,
        'per_page': per_page,
        'sort': sort,
        'direction': direction,
    }


def resolve_pagination(page, per_page, total):
    """Return a clamped page, total_pages, and SQL offset."""
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), total_pages)
    return page, total_pages, (page - 1) * per_page


def pagination_window(page, total_pages, radius=2):
    """Compact page number window with None as an ellipsis marker."""
    if total_pages <= 7:
        return list(range(1, total_pages + 1))

    pages = [1]
    start = max(2, page - radius)
    end = min(total_pages - 1, page + radius)

    if start > 2:
        pages.append(None)

    pages.extend(range(start, end + 1))

    if end < total_pages - 1:
        pages.append(None)

    pages.append(total_pages)
    return pages


def sort_direction_sql(direction):
    return 'DESC' if direction == 'desc' else 'ASC'
