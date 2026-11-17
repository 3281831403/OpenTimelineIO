#!/usr/bin/env python
#
# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the OpenTimelineIO project

"""otiotool is a multipurpose command line tool for inspecting,
modifying, combining, and splitting OTIO files.

Each of the many operations it can perform is provided by a
small, simple utility function. These functions also serve
as concise examples of how OTIO can be used to perform common
workflow tasks."""

import argparse
import os
import pathlib
import re
import sys

from urllib.request import urlopen

from copy import deepcopy

import opentimelineio as otio


def main():
    """otiotool main program.
    This function is responsible for executing the steps specified
    by all of the command line arguments in the right order.
    """

    args = parse_arguments()

    # Special case option, which skips all the other phases

    if args.list_versions:
        print("Available versions for --downgrade FAMILY:VERSION")
        for family, mapping in otio.versioning.full_map().items():
            for label in mapping.keys():
                print(f"  {family}:{label}")
        return

    # Phase 1: Input...

    # Most of this function will operate on this list of timelines.
    # Often there will be just one, but this tool in general enough
    # to operate on several. This is essential when the --stack or
    # --concatenate arguments are used.
    if args.input:
        timelines = read_inputs(args.input)
    else:
        timelines = []

    # Phase 2: Filter (remove stuff)...

    if args.video_only:
        for timeline in timelines:
            keep_only_video_tracks(timeline)

    if args.audio_only:
        for timeline in timelines:
            keep_only_audio_tracks(timeline)

    if args.remove_transitions:
        timelines = filter_transitions(timelines)

    if args.remove_effects:
        timelines = filter_effects(timelines)

    if args.only_tracks_with_name or args.only_tracks_with_index:
        timelines = filter_tracks(
            args.only_tracks_with_name,
            args.only_tracks_with_index,
            timelines
        )

    if args.only_clips_with_name or args.only_clips_with_name_regex:
        timelines = filter_clips(
            args.only_clips_with_name,
            args.only_clips_with_name_regex,
            timelines
        )

    if args.trim:
        for timeline in timelines:
            trim_timeline(args.trim[0], args.trim[1], timeline)

    # Phase 3: Combine timelines

    if args.stack:
        timelines = [stack_timelines(timelines)]

    if args.concat:
        timelines = [concatenate_timelines(timelines)]

    # Phase 4: Combine (or add) tracks

    if args.flatten:
        for timeline in timelines:
            flatten_timeline(
                timeline,
                which_tracks=args.flatten,
                keep=args.keep_flattened_tracks
            )

    # Phase 5: Relinking media

    if args.relink_by_name:
        for timeline in timelines:
            for folder in args.relink_by_name:
                relink_by_name(timeline, folder)

    if args.copy_media_to_folder:
        for timeline in timelines:
            copy_media_to_folder(timeline, args.copy_media_to_folder)

    # Phase 6: Remove/Redaction

    if args.remove_metadata_key:
        for timeline in timelines:
            for key in args.remove_metadata_key:
                remove_metadata_key(timeline, key)

    if args.redact:
        for timeline in timelines:
            redact_timeline(timeline)

    # Phase 7: Inspection

    if args.stats:
        for timeline in timelines:
            print_timeline_stats(timeline)

    if args.inspect:
        for timeline in timelines:
            inspect_timelines(args.inspect, timeline)

    should_summarize = (args.list_clips or
                        args.list_media or
                        args.verify_media or
                        args.list_tracks or
                        args.list_markers or
                        args.verify_ranges)
    if should_summarize:
        for timeline in timelines:
            summarize_timeline(
                args.list_tracks,
                args.list_clips,
                args.list_media,
                args.verify_media,
                args.list_markers,
                args.verify_ranges,
                timeline)

    # Final Phase: Output

    if args.downgrade:
        if ":" in args.downgrade:
            label = args.downgrade
        else:
            label = "OTIO_CORE:" + args.downgrade
        os.environ["OTIO_DEFAULT_TARGET_VERSION_FAMILY_LABEL"] = label

    if args.output:
        # Gather all of the timelines under one OTIO object
        # in preparation for final output
        if len(timelines) > 1:
            output = otio.schema.SerializableCollection()
            output.extend(timelines)
        else:
            output = timelines[0]

        write_output(args.output, output)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="""
otiotool = a multi-purpose command line utility for working with OpenTimelineIO.

This tool works in phases, as follows:
1. Input
    Input files provided by the "--input <filename>" argument(s) are read into
    memory. Files may be OTIO format, or any format supported by adapter
    plugins.

2. Filtering
    Options such as --video-only, --audio-only, --only-tracks-with-name,
    --only-tracks-with-index, --only-clips-with-name,
    --only-clips-with-name-regex, --remove-transitions, --remove-effects and
    --trim will remove content. Only the tracks, clips, etc. that pass all of
    the filtering options provided are passed to the next phase.

3. Combine
    If specified, the --stack, or --concat operations are
    performed (in that order) to combine all of the input timeline(s) into one.

4. Flatten
    If --flatten is specified, then multiple tracks are flattened into one.

5. Relink
    The --relink-by-name option, will scan the specified folder(s) looking for
    files which match the name of each clip in the input timeline(s).
    If matching files are found, clips will be relinked to those files (using
    file:// URLs). Clip names are matched to filenames ignoring file extension.
    If specified, the --copy-media-to-folder option, will copy or download
    all linked media, and relink the OTIO to reference the local copies.
6. Remove/Redact
    The --remove-metadata-key option allows you to remove a specific piece of
    metadata from all objects.
    If specified, the --redact option, will remove ALL metadata and rename all
    objects in the OTIO with generic names (e.g. "Track 1", "Clip 17", etc.)

7. Inspect
    Options such as --stats, --list-clips, --list-tracks, --list-media,
    --verify-media, --list-markers, --verify-ranges, and --inspect
    will examine the OTIO and print information to standard output.

8. Output
    Finally, if the "--output <filename>" option is specified, the resulting
    OTIO will be written to the specified file. The extension of the output
    filename is used to determine the format of the output (e.g. OTIO or any
    format supported by the adapter plugins.) If you need to output an older
    schema version, see the --downgrade option.
""".strip(),
        epilog="""Examples:

Combine multiple files into one, by joining them end-to-end:
otiotool -i titles.otio -i feature.otio -i credits.otio --concat -o full.otio

Layer multiple files on top of each other in a stack:
otiotool -i background.otio -i foreground.otio --stack -o composite.otio

Verify that all referenced media files are accessible:
otiotool -i playlist.otio --verify-media

Inspect specific audio clips in detail:
otiotool -i playlist.otio --only-audio --list-tracks --inspect "Interview"
""",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # Input...
    parser.add_argument(
        "--input",
        "-i",
        type=str,
        nargs='+',
        metavar='PATH(s)',
        help="""Input file path(s). All formats supported by adapter plugins
        are supported. Use '-' to read OTIO from standard input."""
    )

    # Filter...
    track_type_group = parser.add_mutually_exclusive_group()
    track_type_group.add_argument(
        "--video-only",
        "-v",
        action='store_true',
        help="Output only video tracks"
    )
    track_type_group.add_argument(
        "--audio-only",
        "-a",
        action='store_true',
        help="Output only audio tracks"
    )

    parser.add_argument(
        "--only-tracks-with-name",
        type=str,
        nargs='+',
        metavar='NAME(s)',
        help="Output tracks with these name(s)"
    )
    parser.add_argument(
        "--only-tracks-with-index",
        type=int,
        nargs='+',
        metavar='INDEX(es)',
        help="Output tracks with these indexes"
        " (1 based, in same order as --list-tracks)"
    )
    parser.add_argument(
        "--only-clips-with-name",
        type=str,
        nargs='+',
        metavar='NAME(s)',
        help="Output only clips with these name(s)"
    )
    parser.add_argument(
        "--only-clips-with-name-regex",
        type=str,
        nargs='+',
        metavar='REGEX(es)',
        help="""Output only clips with names matching the given
        regular expression pattern (e.g. '[Ii]nterview [0-9]+')"""
    )
    parser.add_argument(
        "--remove-transitions",
        action='store_true',
        help="Remove all transitions"
    )
    parser.add_argument(
        "--remove-effects",
        action='store_true',
        help="Remove all effects"
    )
    parser.add_argument(
        "--trim",
        "-t",
        type=str,
        nargs=2,
        metavar=('START', 'END'),
        help="""Trim from START to END, each specified as seconds or
        HH:MM:SS:FF timecode (matching the timeline's rate)"""
    )

    # Combine...
    parser.add_argument(
        "--flatten",
        "-f",
        choices=['video', 'audio', 'all'],
        metavar='TYPE',
        help="""Flatten multiple tracks into one.
        TYPE must be 'video' or 'audio' or 'all'."""
    )
    parser.add_argument(
        "--keep-flattened-tracks",
        action='store_true',
        help="""When used with --flatten, the new flat track is added above the
        others instead of replacing them."""
    )

    combine_group = parser.add_mutually_exclusive_group()
    combine_group.add_argument(
        "--stack",
        "-s",
        action='store_true',
        help="Stack multiple input files into one timeline"
    )
    combine_group.add_argument(
        "--concat",
        "-c",
        action='store_true',
        help="Concatenate multiple input files end-to-end into one timeline"
    )

    # Relink
    relink_group = parser.add_mutually_exclusive_group()
    relink_group.add_argument(
        "--relink-by-name",
        type=str,
        nargs='+',
        metavar='FOLDER(s)',
        help="""Scan the specified folder looking for filenames which match
        each clip's name. If found, clips are relinked to those files."""
    )
    relink_group.add_argument(
        "--copy-media-to-folder",
        type=str,
        metavar='FOLDER',
        help="""Copy or download all linked media to the specified folder and
        relink all media references to the copies"""
    )

    # Remove/Redact
    parser.add_argument(
        "--remove-metadata-key",
        type=str,
        nargs='+',
        metavar='KEY(s)',
        help="""Remove one or more metadata dictionary top-level keys from all
        objects."""
    )
    parser.add_argument(
        "--redact",
        action='store_true',
        help="""Remove all metadata, names, etc. leaving only the timeline
        structure"""
    )

    # Inspect...
    parser.add_argument(
        "--stats",
        action='store_true',
        help="""List statistics about the result, including start, end, and
        duration"""
    )
    parser.add_argument(
        "--list-clips",
        action='store_true',
        help="List each clip's name"
    )
    parser.add_argument(
        "--list-tracks",
        action='store_true',
        help="List each track's name"
    )
    parser.add_argument(
        "--list-media",
        action='store_true',
        help="List each referenced media URL"
    )
    parser.add_argument(
        "--verify-media",
        action='store_true',
        help="""Verify that each referenced media URL exists (for local media
        only)"""
    )
    parser.add_argument(
        "--list-markers",
        action='store_true',
        help="List summary of all markers"
    )
    parser.add_argument(
        "--verify-ranges",
        action='store_true',
        help="""Verify that each clip in a timeline has a source range
        within the available range of media
        (acceptable in some use cases, not in others)"""
    )
    parser.add_argument(
        "--inspect",
        type=str,
        nargs='+',
        metavar='NAME(s)',
        help="""Inspect details of clips with names matching the given regular
        expression pattern (e.g. 'SFX.*-TEMP')"""
    )

    # Output...
    parser.add_argument(
        "--downgrade",
        type=str,
        metavar='FAMILY:VERSION',
        help="""Downgrade OTIO schema. Only relevant when --output is used
        to output an OTIO file. FAMILY:VERSION specifies which schema family
        and version to use. If FAMILY: is omitted, the default OTIO_CORE: is
        used. For example `--downgrade OTIO_CORE:0.14.0` is equivalent to
        `--downgrade 0.14.0`. See
        https://opentimelineio.readthedocs.io/en/latest/tutorials/versioning-schemas.html
        for details."""
    )
    parser.add_argument(
        "--list-versions",
        action='store_true',
        help="""List available versions for the --downgrade option."""
    )

    parser.add_argument(
        "--output",
        "-o",
        type=str,
        metavar='PATH',
        help="""Output file. All formats supported by adapter plugins
        are supported. Use '-' to write OTIO to standard output."""
    )

    args = parser.parse_args()

    # At least one of these must be specified
    if not any([args.input, args.list_versions]):
        parser.error("Must specify at least one of --input or --list-versions.")

    # Check some options combination.
    if args.keep_flattened_tracks and not args.flatten:
        parser.error("Cannot use --keep-flattened-tracks without also using --flatten.")

    return args


def read_inputs(input_paths):
    """Read one or more timlines from the list of file paths given.
    If a file path is '-' then a timeline is read from stdin.
    """
    timelines = []
    for input_path in input_paths:
        if input_path == '-':
            text = sys.stdin.read()
            timeline = otio.adapters.read_from_string(text, 'otio_json')
        else:
            timeline = otio.adapters.read_from_file(input_path)
        timelines.append(timeline)
    return timelines


def keep_only_video_tracks(timeline):
    """Remove all tracks except for video tracks from a timeline."""
    timeline.tracks[:] = timeline.video_tracks()


def keep_only_audio_tracks(timeline):
    """Remove all tracks except for audio tracks from a timeline."""
    timeline.tracks[:] = timeline.audio_tracks()


def filter_transitions(timelines):
    """Return a copy of the input timelines with all transitions removed.
    The overall duration of the timelines should not be affected."""
    def _f(item):
        if isinstance(item, otio.schema.Transition):
            return None
        return item
    return [otio.algorithms.filtered_composition(t, _f) for t in timelines]


def filter_effects(timelines):
    """Remove all effects from the input timelines. The inputs are modified
    in place, and also returned."""
    for timeline in timelines:
        # Items have an effects attribute, but other Composables do not.
        # (e.g. Transitions) so we need to find only Items.
        for item in timeline.find_children(descended_from_type=otio.core.Item):
            # Clear the effects list contents
            item.effects[:] = []
    return timelines


def _filter(item, names, patterns):
    """This is a helper function that returns the input item if
    its name matches the list of names given (if any), or matches any of the
    patterns given (if any). If the item's name does not match any of the
    given names or patterns, then None is returned."""
    if names and item.name in names:
        return item
    if patterns:
        for pattern in patterns:
            if re.search(pattern, item.name):
                return item
    return None

    # TODO: Should this return a same-duration Gap instead?
    # gap = otio.schema.Gap(source_range=item.trimmed_range())
    # return gap


def filter_tracks(only_tracks_with_name, only_tracks_with_index, timelines):
    """Return a copy of the input timelines with only tracks that match
    either the list of names given, or the list of track indexes given."""

    # Use a variable saved within this function so that the closure
    # below can modify it.
    # See: https://stackoverflow.com/questions/21959985/why-cant-python-increment-variable-in-closure   # noqa: E501
    filter_tracks.index = 0

    def _f(item):
        if not isinstance(item, otio.schema.Track):
            return item
        filter_tracks.index = filter_tracks.index + 1
        if only_tracks_with_index and filter_tracks.index not in only_tracks_with_index:
            return None
        if only_tracks_with_name and item.name not in only_tracks_with_name:
            return None
        return item

    return [otio.algorithms.filtered_composition(t, _f) for t in timelines]


def filter_clips(only_clips_with_name, only_clips_with_name_regex, timelines):
    """Return a copy of the input timelines with only clips with names
    that match either the given list of names, or regular expression patterns."""

    def _f(item):
        if not isinstance(item, otio.schema.Clip):
            return item
        return _filter(item, only_clips_with_name, only_clips_with_name_regex)

    return [otio.algorithms.filtered_composition(t, _f) for t in timelines]


def stack_timelines(timelines):
    """Return a single timeline with all of the tracks from all of the input
    timelines stacked on top of each other. The resulting timeline should be
    as long as the longest input timeline."""
    name = f"Stacked {len(timelines)} Timelines"
    stacked_timeline = otio.schema.Timeline(name)
    for timeline in timelines:
        stacked_timeline.tracks.extend(deepcopy(timeline.tracks[:]))
    return stacked_timeline


def concatenate_timelines(timelines):
    """Return a single timeline with all of the input timelines concatenated
    end-to-end. The resulting timeline should be as long as the sum of the
    durations of the input timelines."""
    name = f"Concatenated {len(timelines)} Timelines"
    concatenated_track = otio.schema.Track()
    for timeline in timelines:
        concatenated_track.append(deepcopy(timeline.tracks))
    concatenated_timeline = otio.schema.Timeline(
        name=name,
        tracks=[concatenated_track]
    )
    return concatenated_timeline


def flatten_timeline(timeline, which_tracks='video', keep=False):
    """Replace the tracks of this timeline with a single track by flattening.
    If which_tracks is specified, you may choose 'video', 'audio', or 'all'.
    If keep is True, then the old tracks are retained and the new one is added
    above them instead of replacing them. This can be useful to see and
    understand how flattening works."""

    # Make two lists: tracks_to_flatten and other_tracks
    # Note: that we take care to NOT assume that there are only two kinds
    # of tracks.
    if which_tracks == 'all':
        tracks_to_flatten = timeline.tracks
        other_tracks = []
        kind = tracks_to_flatten[0].kind
    elif which_tracks == 'video':
        tracks_to_flatten = timeline.video_tracks()
        other_tracks = [t for t in timeline.tracks if t not in tracks_to_flatten]
        kind = otio.schema.TrackKind.Video
    elif which_tracks == 'audio':
        tracks_to_flatten = timeline.audio_tracks()
        other_tracks = [t for t in timeline.tracks if t not in tracks_to_flatten]
        kind = otio.schema.TrackKind.Audio
    else:
        raise ValueError(
            "Invalid choice {} for which_tracks argument"
            " to flatten_timeline.".format(which_tracks)
        )

    flat_track = otio.algorithms.flatten_stack(tracks_to_flatten[:])
    flat_track.kind = kind

    if keep:
        timeline.tracks.append(flat_track)
    else:
        timeline.tracks[:] = other_tracks + [flat_track]


def time_from_string(text, rate):
    """This helper function turns a string into a RationalTime. It accepts
    either a timecode string (e.g. "HH:MM:SS:FF") or a string with a floating
    point value measured in seconds. The second argument to this function
    specifies the rate for the returned RationalTime."""
    if ":" in text:
        return otio.opentime.from_timecode(text, rate)
    else:
        return otio.opentime.from_seconds(float(text), rate)


def trim_timeline(start, end, timeline):
    """Return a copy of the input timeline trimmed to the start and end
    times given. Each of the start and end times can be specified as either
    a timecode string (e.g. "HH:MM:SS:FF") or a string with a floating
    point value measured in seconds."""
    if timeline.global_start_time is not None:
        rate = timeline.global_start_time.rate
    else:
        rate = timeline.duration().rate
    try:
        start_time = time_from_string(start, rate)
        end_time = time_from_string(end, rate)
    except Exception:
        raise ValueError("Start and end arguments to --trim must be "
                         "either HH:MM:SS:FF or a floating point number of"
                         " seconds, not '{}' and '{}'".format(start, end))
    trim_range = otio.opentime.range_from_start_end_time(start_time, end_time)
    timeline.tracks[:] = [
        otio.algorithms.track_trimmed_to_range(t, trim_range)
        for t in timeline.tracks
    ]


def remove_metadata_key(timeline, key):
    def rem(d):
        if key in d:
            del d[key]

    rem(timeline.metadata)
    for child in [timeline.tracks] + list(timeline.find_children()):
        rem(child.metadata)
        if hasattr(child, 'markers'):
            for marker in child.markers:
                rem(marker.metadata)
        if hasattr(child, 'effects'):
            for effect in child.effects:
                rem(effect.metadata)
        if hasattr(child, 'media_reference'):
            media_reference = child.media_reference
            if media_reference:
                rem(media_reference.metadata)


# Used only within _counter() to keep track of object indexes
__counters = {}


def _counter(name):
    """This is a helper function for returning redacted names, based on a name."""
    counter = __counters.get(name, 0)
    counter += 1
    __counters[name] = counter
    return counter


def redact_timeline(timeline):
    """Remove all metadata, names, or other identifying information from this
    timeline. Only the structure, schema and timing will remain."""

    counter = _counter(timeline.schema_name())
    timeline.name = f"{timeline.schema_name()} #{counter}"
    timeline.metadata.clear()

    for child in [timeline.tracks] + list(timeline.find_children()):
        counter = _counter(child.schema_name())
        child.name = f"{child.schema_name()} #{counter}"
        child.metadata.clear()
        if hasattr(child, 'markers'):
            for marker in child.markers:
                counter = _counter(marker.schema_name())
                marker.name = f"{marker.schema_name()} #{counter}"
                marker.metadata.clear()
        if hasattr(child, 'effects'):
            for effect in child.effects:
                counter = _counter(effect.schema_name())
                effect.name = f"{effect.schema_name()} #{counter}"
                effect.metadata.clear()
        if hasattr(child, 'media_reference'):
            media_reference = child.media_reference
            if media_reference:
                counter = _counter(media_reference.schema_name())
                has_target_url = hasattr(media_reference, 'target_url')
                if has_target_url and media_reference.target_url:
                    media_reference.target_url = f"URL #{counter}"
                media_reference.name = f"{media_reference.schema_name()} #{counter}"
                media_reference.metadata.clear()


def copy_media(url, destination_path):
    # file:// URLs and plain paths both refer to local files
    source_path = otio.url_utils.filepath_from_url(url)
    if "://" not in source_path:
        print(f"COPYING: {url}")
        with open(source_path, "rb") as source_file:
            data = source_file.read()
    else:
        print(f"DOWNLOADING: {url}")
        data = urlopen(url).read()
    with open(destination_path, "wb") as destination_file:
        destination_file.write(data)
    return destination_path


def _image_sequence_frames_used(clip, media_reference):
    """Return the list of image sequence frame numbers that the clip
    actually uses, or None if they cannot be determined."""
    available_range = media_reference.available_range
    if available_range is None:
        return None
    try:
        used_range = clip.trimmed_range()
    except Exception:
        return None

    # Clamp to the frames that exist in the sequence
    start = max(used_range.start_time, available_range.start_time)
    end = min(
        used_range.end_time_inclusive(),
        available_range.end_time_inclusive()
    )
    if end < start:
        return []

    try:
        first_frame = media_reference.frame_for_time(start)
        last_frame = media_reference.frame_for_time(end)
    except Exception:
        return None

    step = media_reference.frame_step or 1
    return list(range(first_frame, last_frame + 1, step))


def _image_sequence_url_for_frame(media_reference, frame):
    """Return the URL of the given frame number of an image sequence."""
    step = media_reference.frame_step or 1
    image_number = (frame - media_reference.start_frame) // step
    return media_reference.target_url_for_image_number(image_number)


def copy_media_to_folder(timeline, folder):
    """Copy or download all referenced media to this folder, and relink media
    references to the copies."""

    # @TODO: Add an option to allow mkdir
    # if not os.path.exists(folder):
    #     os.mkdir(folder)

    # Each distinct source is copied only once, even if several clips
    # reference it. file:// URLs and plain paths to the same file are
    # considered the same source.
    destination_for_source = {}
    # Different sources with the same file name are given distinct
    # destination names, so they cannot overwrite each other.
    source_for_dest_name = {}

    def _source_key(url):
        path = otio.url_utils.filepath_from_url(url)
        if "://" not in path:
            path = os.path.abspath(path)
        return path

    def _claim_dest_name(filename, key):
        """Return a destination file name within the folder for the given
        source, made unique if a different source already claimed it."""
        if (filename not in source_for_dest_name or
                source_for_dest_name[filename] == key):
            source_for_dest_name[filename] = key
            return filename
        stem, ext = os.path.splitext(filename)
        index = 1
        while True:
            candidate = f"{stem}_{index}{ext}"
            if (candidate not in source_for_dest_name or
                    source_for_dest_name[candidate] == key):
                source_for_dest_name[candidate] = key
                return candidate
            index += 1

    def _is_same_file(source_key, destination_path):
        if "://" in source_key:
            return False
        if source_key == os.path.abspath(destination_path):
            return True
        try:
            return os.path.samefile(source_key, destination_path)
        except OSError:
            return False

    def _copy_to_folder(url, dest_name):
        """Copy url into the folder under dest_name if needed, and return
        the destination path, or None if the media could not be copied."""
        key = _source_key(url)
        if key in destination_for_source:
            return destination_for_source[key]
        destination_path = os.path.join(folder, dest_name)
        if (_is_same_file(key, destination_path) and
                os.path.exists(destination_path)):
            # The media is already in the folder, nothing to copy. This
            # keeps rerunning the command from adding new files.
            pass
        elif os.path.exists(destination_path):
            print(
                "WARNING: Relinking to existing file"
                " (instead of overwriting it): {}".format(destination_path)
            )
        else:
            try:
                copy_media(url, destination_path)
            except Exception as ex:
                print(f"ERROR: Problem copying/downloading media {ex}")
                return None
        destination_for_source[key] = destination_path
        return destination_path

    def _copy_external_reference(media_reference):
        url = media_reference.target_url
        key = _source_key(url)
        filename = os.path.basename(otio.url_utils.filepath_from_url(url))
        destination_path = _copy_to_folder(
            url, _claim_dest_name(filename, key)
        )
        if destination_path is not None:
            media_reference.target_url = destination_path

    def _copy_image_sequence(clip, media_reference):
        frames = _image_sequence_frames_used(clip, media_reference)
        if frames is None:
            print(
                "WARNING: Cannot determine which frames of the image"
                f" sequence are used by clip {clip.name};"
                " it was not copied."
            )
            return
        if not frames:
            return

        urls = [
            _image_sequence_url_for_frame(media_reference, frame)
            for frame in frames
        ]
        keys = [_source_key(url) for url in urls]
        filenames = [
            os.path.basename(otio.url_utils.filepath_from_url(url))
            for url in urls
        ]

        # The destination names must keep the
        # "name_prefix + frame number + name_suffix" pattern, otherwise the
        # rewritten reference could not resolve each frame to its copy.
        # If any of the frame file names is already claimed by a different
        # source, adjust the name prefix of the whole sequence instead.
        prefix = media_reference.name_prefix
        attempt = 0
        while True:
            new_prefix = prefix if attempt == 0 else f"{prefix}_{attempt}"
            dest_names = [
                new_prefix + filename[len(prefix):]
                for filename in filenames
            ]
            conflict = any(
                dest_name in source_for_dest_name and
                source_for_dest_name[dest_name] != key
                for dest_name, key in zip(dest_names, keys)
            )
            if not conflict:
                break
            attempt += 1
        for dest_name, key in zip(dest_names, keys):
            source_for_dest_name[dest_name] = key

        for url, dest_name in zip(urls, dest_names):
            if _copy_to_folder(url, dest_name) is None:
                # The error was already reported; leave this
                # reference unchanged.
                return

        if new_prefix != prefix:
            media_reference.name_prefix = new_prefix
        base = folder if folder.endswith("/") else folder + "/"
        media_reference.target_url_base = base

    for clip in timeline.find_clips():
        media_reference = clip.media_reference
        if media_reference is None:
            continue
        if isinstance(media_reference, otio.schema.ImageSequenceReference):
            _copy_image_sequence(clip, media_reference)
        elif (hasattr(media_reference, 'target_url') and
                media_reference.target_url):
            _copy_external_reference(media_reference)


def relink_by_name(timeline, path):
    """Relink clips in the timeline to media files discovered at the
    given folder path."""

    def _conform_path(p):
        # Turn absolute paths into file:// URIs
        if os.path.isabs(p):
            return pathlib.Path(p).as_uri()
        else:
            # Leave relative paths as-is
            return p

    count = 0
    if os.path.isdir(path):
        name_to_url = dict([
            (
                os.path.splitext(x)[0],
                _conform_path(os.path.join(path, x))
            )
            for x in os.listdir(path)
        ])
    elif os.path.isfile(path):
        print((f"ERROR: Cannot relink to '{path}':"
               " Please specify a folder instead of a file."))
        return
    else:
        print(f"ERROR: Cannot relink to '{path}': No such file or folder.")
        return

    for clip in timeline.find_clips():
        url = name_to_url.get(clip.name)
        if url is not None:
            old_reference = clip.media_reference
            new_reference = otio.schema.ExternalReference(target_url=url)
            if old_reference is not None:
                # Relinking only changes which file the reference points
                # to. The timing and metadata of the previous reference
                # still apply, so carry them over to the new one.
                new_reference.available_range = old_reference.available_range
                new_reference.metadata.update(old_reference.metadata)
            clip.media_reference = new_reference
            count += 1

    print(f"Relinked {count} clips to files in folder {path}")


def print_timeline_stats(timeline):
    """Print some statistics about the given timeline."""
    print(f"Name: {timeline.name}")
    trimmed_range = timeline.tracks.trimmed_range()
    print("Start:    {}\nEnd:      {}\nDuration: {}".format(
        otio.opentime.to_timecode(trimmed_range.start_time),
        otio.opentime.to_timecode(trimmed_range.end_time_exclusive()),
        otio.opentime.to_timecode(trimmed_range.duration),
    ))


def inspect_timelines(name_regex, timeline):
    """Print some detailed information about the item(s) in the timeline with names
    that match the given regular expression."""
    print("TIMELINE:", timeline.name)
    items_to_inspect = [_filter(item, [], name_regex)
                        for item in timeline.find_children()]
    items_to_inspect = list(filter(None, items_to_inspect))
    for item in items_to_inspect:
        print(f"  ITEM: {item.name} ({type(item)})")
        print("    source_range:", item.source_range)
        print("    trimmed_range:", item.trimmed_range())
        print("    visible_range:", item.visible_range())
        try:
            print("    available_range:", item.available_range())
        except Exception:
            pass
        print("    range_in_parent:", item.range_in_parent())
        print(
            "    trimmed range in timeline:",
            item.transformed_time_range(
                item.trimmed_range(), timeline.tracks
            )
        )
        print(
            "    visible range in timeline:",
            item.transformed_time_range(
                item.visible_range(), timeline.tracks
            )
        )
        ancestor = item.parent()
        while ancestor is not None:
            print(
                "    range in {} ({}): {}".format(
                    ancestor.name,
                    type(ancestor),
                    item.transformed_time_range(item.trimmed_range(), ancestor)
                )
            )
            ancestor = ancestor.parent()


def summarize_timeline(list_tracks, list_clips, list_media, verify_media,
                       list_markers, verify_ranges, timeline):
    """Print a summary of a timeline, optionally listing the tracks, clips, media,
    and/or markers inside it."""
    print("TIMELINE:", timeline.name)
    for child in [timeline.tracks] + list(timeline.find_children()):
        if isinstance(child, otio.schema.Track):
            if list_tracks:
                print(f"TRACK: {child.name} ({child.kind})")
        if isinstance(child, otio.schema.Clip):
            if list_clips or verify_ranges:
                if verify_ranges:
                    range_msg = ""
                    try:
                        source = child.source_range
                        available = child.available_range()

                        # contains() uses end_time_exclusive(),
                        # does not handle case when
                        # the end of the source range
                        # meets available range exactly
                        available_start = available.start_time
                        available_end = available.end_time_inclusive()
                        src_start = source.start_time
                        src_end = source.end_time_inclusive()
                        if src_start < available_start or available_end < src_end:
                            range_msg = "SOURCE MEDIA OUT OF BOUNDS"
                        else:
                            range_msg = "IN BOUNDS"
                    except Exception:  # available range is, well, unavailable
                        pass
                    print("  CLIP:", child.name, range_msg)
                else:
                    print("  CLIP:", child.name)
            if list_media or verify_media:
                media_reference = child.media_reference
                if isinstance(
                        media_reference,
                        otio.schema.ImageSequenceReference):
                    # An image sequence references one file per frame;
                    # list each frame the clip actually uses.
                    frames = _image_sequence_frames_used(
                        child, media_reference
                    )
                    if frames is None:
                        urls = [
                            media_reference.abstract_target_url(
                                "#" * max(
                                    1, media_reference.frame_zero_padding
                                )
                            )
                        ]
                        verify_urls = False
                    else:
                        urls = [
                            _image_sequence_url_for_frame(
                                media_reference, frame
                            )
                            for frame in frames
                        ]
                        verify_urls = True
                    for url in urls:
                        detail = ""
                        if verify_media and verify_urls:
                            if os.path.exists(
                                    otio.url_utils.filepath_from_url(url)):
                                detail = " EXISTS"
                            else:
                                detail = " NOT FOUND"
                        print(f"    MEDIA{detail}: {url}")
                else:
                    try:
                        url = media_reference.target_url
                    except Exception:
                        url = None
                    detail = ""
                    if verify_media and url:
                        # file:// URLs and plain paths are equivalent
                        if os.path.exists(
                                otio.url_utils.filepath_from_url(url)):
                            detail = " EXISTS"
                        else:
                            detail = " NOT FOUND"
                    print(f"    MEDIA{detail}: {url}")

        if list_markers and hasattr(child, 'markers'):
            top_level = child
            while top_level.parent() is not None:
                top_level = top_level.parent()
            for marker in child.markers:
                template = "  MARKER: global: {} local: {} duration: {} color: {} name: {}"  # noqa: E501
                print(template.format(
                    otio.opentime.to_timecode(child.transformed_time(
                        marker.marked_range.start_time,
                        top_level)),
                    otio.opentime.to_timecode(marker.marked_range.start_time),
                    marker.marked_range.duration.value,
                    marker.color,
                    marker.name
                ))


def write_output(output_path, output):
    """Write the given OTIO object to a file path. If the file path given is
    the string '-' then the output is written to stdout instead."""
    if output_path == '-':
        result = otio.adapters.write_to_string(output)
        print(result)
    else:
        otio.adapters.write_to_file(output, output_path)


if __name__ == '__main__':
    main()
