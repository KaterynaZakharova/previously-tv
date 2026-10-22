"""Order supported events across the current story prefix without an opening bias."""
from .watched_scope import covers, windows


def event_end(event):
    return event.get('end', max((r['end'] for r in event.get('ranges', [])), default=event['start']))


def cutoff_seconds(manifest):
    spans = windows(manifest)
    return max((b for _, b in spans), default=0.) if spans is not None else None


def order_for_playhead(events, manifest, analysis, state, limit=18):
    """Give equal-duration parts equal selection opportunities, then play in order.

    This orders evidence already allowed by the source cutoff. It never marks a
    scene as actually watched and never introduces footage after the playhead.
    """
    cutoff = cutoff_seconds(manifest)
    spans = windows(manifest)
    events = [e for e in events if covers(spans, e['start'], event_end(e))
              and all(covers(spans, r['start'], r['end']) for r in e.get('ranges', []))]
    forgotten = [e for e in analysis['events'] if state.get('memory', {}).get(e['id']) == 0]
    if forgotten:
        focus = next((e for e in forgotten if e['id'] == state.get('last_rated')), forgotten[-1])
        threads = set(focus.get('threads', []));people = set(focus.get('characters', []))
        # Missing-memory context must set this event up, rather than fill the
        # next positions with unrelated or later events.
        events = [e for e in events if event_end(e) <= focus['start'] + 1e-6
                  and (set(e.get('threads', [])) & threads or set(e.get('characters', [])) & people)]
        return sorted(events, key=lambda e: (
            -len(set(e.get('threads', [])) & threads),
            -len(set(e.get('characters', [])) & people), abs(focus['start'] - event_end(e)), e['id']))
    chronological = sorted(events, key=lambda e: (e['start'], event_end(e), e['id']))
    if len(events) <= limit:
        return chronological
    if cutoff is None:
        cutoff = analysis.get('duration') or max((event_end(e) for e in events), default=0.)
    if cutoff <= 0 or limit <= 0:
        return []
    buckets = [[] for _ in range(limit)]
    for event in events:
        midpoint = (event['start'] + event_end(event)) / 2
        buckets[min(limit - 1, int(limit * midpoint / cutoff))].append(event)
    selected = []
    for index, bucket in enumerate(buckets):
        center = (index + .5) * cutoff / limit
        if bucket:
            selected.append(min(bucket, key=lambda e: (
                -(e.get('category') == 'critical'), abs((e['start'] + event_end(e)) / 2 - center), e['id'])))
    chosen = {e['id'] for e in selected}
    remaining = [e for e in chronological if e['id'] not in chosen]
    # Empty time intervals have no supported event. Fill unused slots from the
    # least represented remaining locations, without inventing missing footage.
    while remaining and len(selected) < limit:
        event = min(remaining, key=lambda e: (
            -(e.get('category') == 'critical'),
            -min((abs((e['start'] + event_end(e)) / 2 - (s['start'] + event_end(s)) / 2)
                  for s in selected), default=cutoff), e['id']))
        selected.append(event); remaining.remove(event)
    selected.sort(key=lambda e: (e['start'], event_end(e), e['id']))
    return selected + remaining


def selection_context(manifest):
    cutoff = cutoff_seconds(manifest)
    if cutoff is None:
        return {}
    return dict(story_cutoff_seconds=cutoff,
                selection_goal='Give all eligible time periods equal consideration, without favoring the beginning, middle or end. Select useful events across the whole available story and present the initial recap chronologically from the beginning. Only Remember / Not remember feedback should shift attention toward a particular story period. Skipped earlier scenes are eligible.')
