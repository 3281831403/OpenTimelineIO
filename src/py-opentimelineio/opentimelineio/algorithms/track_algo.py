# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the OpenTimelineIO project

"""Algorithms for track objects."""

import copy

from .. import (
    schema,
    exceptions,
    opentime,
)


def track_trimmed_to_range(in_track, trim_range):
    """
    Returns a new track that is a copy of the in_track, but with items
    outside the trim_range removed and items on the ends trimmed to the
    trim_range.

    .. note:: The track is never expanded, only shortened.

    The trim_range is expressed in the track's parent space (the timeline's
    space). The resulting track's duration is the intersection of the
    trim_range with the range the track originally presented. Items fully
    inside the trim_range are left untouched, items partially overlapped
    are trimmed at the boundary, and any blank space between the start of
    the trim_range and the first remaining item is preserved as a
    :py:class:`.Gap` so the remaining content does not slip forward.

    Please note that you could do nearly the same thing non-destructively by
    just setting the :py:class:`.Track`\'s source_range but sometimes you want
    to really cut away the stuff outside and that's what this function is meant for.

    :param Track in_track: Track to trim
    :param TimeRange trim_range:
    :returns: New trimmed track
    :rtype: Track
    """
    new_track = copy.deepcopy(in_track)

    # The trim_range is expressed in the track's parent space. If the track
    # has a source_range, its children live in a different space, so figure
    # out which part of the child space the trim_range refers to.
    if new_track.source_range is not None:
        presented_range = new_track.source_range
    else:
        presented_range = opentime.TimeRange(
            start_time=opentime.RationalTime(
                0,
                trim_range.start_time.rate
            ),
            duration=new_track.duration()
        )

    # map the trim_range into the track's child space
    child_trim_range = opentime.TimeRange(
        start_time=trim_range.start_time + presented_range.start_time,
        duration=trim_range.duration
    )

    # only the part of the trim range that overlaps the range the track
    # actually presents can produce any output
    keep_start = max(
        child_trim_range.start_time,
        presented_range.start_time
    )
    keep_end = min(
        child_trim_range.end_time_exclusive(),
        presented_range.end_time_exclusive()
    )

    if keep_end <= keep_start:
        # the trim range does not overlap this track at all
        del new_track[:]
        new_track.source_range = None
        return new_track

    keep_range = opentime.range_from_start_end_time(keep_start, keep_end)

    if keep_range == presented_range:
        # the trim range covers the whole track, nothing to trim
        return new_track

    track_map = new_track.range_of_all_children()

    # iterate backwards so we can delete items
    for c, child in reversed(list(enumerate(new_track))):
        child_range = track_map[child]
        if not keep_range.intersects(child_range):
            # completely outside the trim range, so we discard it
            del new_track[c]
            continue

        # should we trim the start and/or end of this child?
        trim_the_start = keep_range.start_time > child_range.start_time
        trim_the_end = (
            keep_range.end_time_exclusive()
            < child_range.end_time_exclusive()
        )

        if not trim_the_start and not trim_the_end:
            # completely contained (possibly exactly abutting the edges of
            # the trim range), keep the whole thing as-is
            continue

        if isinstance(child, schema.Transition):
            raise exceptions.CannotTrimTransitionsError(
                "Cannot trim in the middle of a Transition."
            )

        # we need to clip the end(s)
        child_source_range = child.trimmed_range()

        # should we trim the start?
        if trim_the_start:
            trim_amount = keep_range.start_time - child_range.start_time
            child_source_range = opentime.TimeRange(
                start_time=child_source_range.start_time + trim_amount,
                duration=child_source_range.duration - trim_amount

            )

        # should we trim the end?
        if trim_the_end:
            trim_amount = (
                child_range.end_time_exclusive()
                - keep_range.end_time_exclusive()
            )
            child_source_range = opentime.TimeRange(
                start_time=child_source_range.start_time,
                duration=child_source_range.duration - trim_amount

            )

        # set the new child's trims
        child.source_range = child_source_range

    if len(new_track) > 0:
        # if the trim range starts before the first remaining child, fill
        # the blank space with a Gap so the remaining content does not
        # slip forward
        first_child_range = track_map[new_track[0]]
        if first_child_range.start_time > keep_range.start_time:
            gap_duration = (
                first_child_range.start_time - keep_range.start_time
            )
            new_track.insert(
                0,
                schema.Gap(
                    source_range=opentime.TimeRange(
                        start_time=opentime.RationalTime(
                            0,
                            gap_duration.rate
                        ),
                        duration=gap_duration
                    )
                )
            )

        # same for any blank space at the end of the trim range
        last_child_range = track_map[new_track[-1]]
        last_child_end = last_child_range.end_time_exclusive()
        if last_child_end < keep_range.end_time_exclusive():
            gap_duration = keep_range.end_time_exclusive() - last_child_end
            new_track.append(
                schema.Gap(
                    source_range=opentime.TimeRange(
                        start_time=opentime.RationalTime(
                            0,
                            gap_duration.rate
                        ),
                        duration=gap_duration
                    )
                )
            )
    else:
        # the trim range only covers blank space in this track, preserve
        # it as a Gap
        new_track.append(
            schema.Gap(
                source_range=opentime.TimeRange(
                    start_time=opentime.RationalTime(
                        0,
                        keep_range.duration.rate
                    ),
                    duration=keep_range.duration
                )
            )
        )

    # the children have been re-based to exactly cover the trimmed range,
    # so the track no longer needs a source_range of its own
    new_track.source_range = None

    return new_track


def track_with_expanded_transitions(in_track):
    """Expands transitions such that neighboring clips are trimmed into
    regions of overlap.

    For example, if your track is::

        Clip1, T, Clip2

    will return::

        Clip1', (Clip1_t, T, Clip2_t), Clip2'

    Where ``Clip1'`` is the part of ``Clip1`` not in the transition, ``Clip1_t`` is the
    part inside the transition and so on.

    .. note:: The items used in a transition are encapsulated in tuples.

    :param Track in_track: Track to expand
    :returns: Track
    :rtype: list[Track]
    """

    result_track = []

    seq_iter = iter(in_track)
    prev_thing = None
    thing = next(seq_iter, None)
    next_thing = next(seq_iter, None)

    while thing is not None:
        if isinstance(thing, schema.Transition):
            result_track.append(_expand_transition(thing, in_track))
        else:
            # not a transition, but might be trimmed by one before or after
            # in the track
            pre_transition = None
            next_transition = None

            if isinstance(prev_thing, schema.Transition):
                pre_transition = prev_thing

            if isinstance(next_thing, schema.Transition):
                next_transition = next_thing

            result_track.append(
                _trim_from_transitions(
                    thing,
                    pre=pre_transition,
                    post=next_transition
                )
            )

        # loop
        prev_thing = thing
        thing = next_thing
        next_thing = next(seq_iter, None)

    return result_track


def _expand_transition(target_transition, from_track):
    """ Expand transitions into the portions of pre-and-post clips that
    overlap with the transition.
    """

    result = from_track.neighbors_of(
        target_transition,
        schema.NeighborGapPolicy.around_transitions
    )

    trx_duration = target_transition.in_offset + target_transition.out_offset

    # make copies of the before and after, and modify their in/out points
    pre = copy.deepcopy(result[0])

    if isinstance(pre, schema.Transition):
        raise exceptions.TransitionFollowingATransitionError(
            "cannot put two transitions next to each other in a  track: "
            "{}, {}".format(
                pre,
                target_transition
            )
        )
    if target_transition.in_offset is None:
        raise RuntimeError(
            f"in_offset is None on: {target_transition}"
        )

    if target_transition.out_offset is None:
        raise RuntimeError(
            f"out_offset is None on: {target_transition}"
        )

    pre.name = (pre.name or "") + "_transition_pre"

    # ensure that pre.source_range is set, because it will get manipulated
    tr = pre.trimmed_range()

    pre.source_range = opentime.TimeRange(
        start_time=(
            tr.end_time_exclusive() - target_transition.in_offset
        ),
        duration=trx_duration.rescaled_to(
            tr.start_time
        )
    )

    post = copy.deepcopy(result[1])
    if isinstance(post, schema.Transition):
        raise exceptions.TransitionFollowingATransitionError(
            "cannot put two transitions next to each other in a  track: "
            "{}, {}".format(
                target_transition,
                post
            )
        )

    post.name = (post.name or "") + "_transition_post"

    # ensure that post.source_range is set, because it will get manipulated
    tr = post.trimmed_range()

    post.source_range = opentime.TimeRange(
        start_time=(
            tr.start_time - target_transition.in_offset
        ).rescaled_to(tr.start_time),
        duration=trx_duration.rescaled_to(tr.start_time)
    )

    return pre, target_transition, post


def _trim_from_transitions(thing, pre=None, post=None):
    """ Trim clips next to transitions. """

    result = copy.deepcopy(thing)

    # We might not have a source_range yet,
    # We can trim to the computed trimmed_range to
    # ensure we have something.
    new_range = result.trimmed_range()
    start_time = new_range.start_time
    duration = new_range.duration

    if pre:
        start_time += pre.out_offset
        duration -= pre.out_offset

    if post:
        duration -= post.in_offset

    result.source_range = opentime.TimeRange(start_time, duration)

    return result
