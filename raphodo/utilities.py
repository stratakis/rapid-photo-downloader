# Copyright (C) 2007-2022 Damon Lynch <damonlynch@gmail.com>

# This file is part of Rapid Photo Downloader.
#
# Rapid Photo Downloader is free software: you can redistribute it and/or
# modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Rapid Photo Downloader is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Rapid Photo Downloader.  If not,
# see <http://www.gnu.org/licenses/>.

__author__ = "Damon Lynch"
__copyright__ = "Copyright 2007-2022, Damon Lynch"

import contextlib
import site
import locale
import logging
import os
from pathlib import Path
import random
import re
import string
import sys
import tempfile
import time
import tarfile
from collections import namedtuple, defaultdict

from datetime import datetime
from itertools import groupby, zip_longest
from typing import Optional, List, Union, Any, Tuple, Iterator
import struct
import ctypes
import signal
import warnings
import babel
from glob import glob
from pkg_resources import parse_version
import pkg_resources

import arrow
import psutil
from PyQt5.QtCore import QSize, QLocale, QTranslator, QLibraryInfo, QStandardPaths

import raphodo.__about__ as __about__
from raphodo.constants import disable_version_check
from raphodo import localedir, i18n_domain


# Arrow 0.9.0 separated the replace and shift functions into separate calls,
# deprecating using replace() to do the work of the new shift()
# Arrow 0.14.5 removed the deprecated shift functionality from the replace()
try:
    arrow_version = parse_version(arrow.__version__)
except AttributeError:
    arrow_version = None

if arrow_version is not None:
    arrow_shift_support = arrow_version >= parse_version("0.9.0")
else:
    try:
        now = arrow.now()
        now.shift(seconds=1)
        arrow_shift_support = True
    except AttributeError:
        arrow_shift_support = False


# Suppress parsing warnings for 0.14.3 <= Arrow version < 0.15
if arrow_version >= parse_version("0.14.3") and arrow_version < parse_version("0.15.0"):
    from arrow.factory import ArrowParseWarning

    warnings.simplefilter("ignore", ArrowParseWarning)


# Linux specific code to ensure child processes exit when parent dies
# See http://stackoverflow.com/questions/19447603/
# how-to-kill-a-python-child-process-created-with-subprocess-check-output-when-t/
libc = ctypes.CDLL("libc.so.6")


def set_pdeathsig(sig=signal.SIGTERM):
    def callable():
        return libc.prctl(1, sig)

    return callable


def available_cpu_count(physical_only=False) -> int:
    """
    Determine the number of CPUs available.

    A CPU is "available" if cpuset has not restricted the number of
    cpus. Portions of this code from
    http://stackoverflow.com/questions/1006289/how-to-find-out-the-number-of-
    cpus-using-python

    :return available CPU count, or 1 if cannot be determined.
     Value guaranteed to be >= 1.
    """

    # cpuset may restrict the number of *available* processors
    available = None
    if sys.platform.startswith("linux"):
        try:
            status_file = open("/proc/self/status")
            status = status_file.read()
            status_file.close()
        except IOError:
            pass
        else:
            m = re.search(r"(?m)^Cpus_allowed:\s*(.*)$", status)
            if m:
                available = bin(int(m.group(1).replace(",", ""), 16)).count("1")
                if available > 0 and not physical_only:
                    return available

    if physical_only:
        physical = psutil.cpu_count(logical=False)
        if physical is not None:
            if available is not None:
                return min(available, physical)
            return physical

    c = os.cpu_count()
    if c is not None:
        return max(c, 1)
    c = psutil.cpu_count()
    if c is not None:
        return max(c, 1)
    else:
        return 1


def confirm(prompt: Optional[str] = None, resp: Optional[bool] = False) -> bool:
    r"""
    Prompts for yes or no response from the user.

    :param prompt: prompt displayed to user
    :param resp: the default value assumed by the caller when user
     simply types ENTER.
    :return: True for yes and False for no.
    """

    # >>> confirm(prompt='Create Directory?', resp=True)
    # Create Directory? [y]|n:
    # True
    # >>> confirm(prompt='Create Directory?', resp=False)
    # Create Directory? [n]|y:
    # False
    # >>> confirm(prompt='Create Directory?', resp=False)
    # Create Directory? [n]|y: y
    # True

    if prompt is None:
        prompt = "Confirm"

    if resp:
        prompt = "%s [%s]|%s: " % (prompt, "y", "n")
    else:
        prompt = "%s [%s]|%s: " % (prompt, "n", "y")

    while True:
        ans = input(prompt)
        if not ans:
            return resp
        if ans not in ["y", "Y", "n", "N"]:
            print("please enter y or n.")
            continue
        return ans in ["y", "Y"]


@contextlib.contextmanager
def stdchannel_redirected(stdchannel, dest_filename):
    """
    A context manager to temporarily redirect stdout or stderr

    Usage:
    with stdchannel_redirected(sys.stderr, os.devnull):
       do_work()

    Source:
    http://marc-abramowitz.com/archives/2013/07/19/python-context-manager-for-redirected-stdout-and-stderr/
    """
    oldstdchannel = dest_file = None
    try:
        oldstdchannel = os.dup(stdchannel.fileno())
        dest_file = open(dest_filename, "w")
        os.dup2(dest_file.fileno(), stdchannel.fileno())
        yield
    finally:
        if oldstdchannel is not None:
            os.dup2(oldstdchannel, stdchannel.fileno())
        if dest_file is not None:
            dest_file.close()


@contextlib.contextmanager
def show_errors():
    yield


# Translators: these values are file size suffixes like B representing bytes, KB representing
# kilobytes, etc.
suffixes = [
    _("B"),
    _("KB"),
    _("MB"),
    _("GB"),
    _("TB"),
    _("PB"),
    _("EB"),
    _("ZB"),
    _("YB"),
]


def format_size_for_user(
    size_in_bytes: int, zero_string: str = "", no_decimals: int = 2
) -> str:
    r"""
    Humanize display of bytes.

    Uses Microsoft style i.e. 1000 Bytes = 1 KB

    :param size: size in bytes
    :param zero_string: string to use if size == 0

    >>> locale.setlocale(locale.LC_ALL, ('en_US', 'utf-8'))
    'en_US.UTF-8'
    >>> format_size_for_user(0)
    ''
    >>> format_size_for_user(1)
    '1 B'
    >>> format_size_for_user(123)
    '123 B'
    >>> format_size_for_user(1000)
    '1 KB'
    >>> format_size_for_user(1024)
    '1.02 KB'
    >>> format_size_for_user(1024, no_decimals=0)
    '1 KB'
    >>> format_size_for_user(1100, no_decimals=2)
    '1.1 KB'
    >>> format_size_for_user(1000000, no_decimals=2)
    '1 MB'
    >>> format_size_for_user(1000001, no_decimals=2)
    '1 MB'
    >>> format_size_for_user(1020001, no_decimals=2)
    '1.02 MB'
    """

    if size_in_bytes == 0:
        return zero_string
    i = 0
    while size_in_bytes >= 1000 and i < len(suffixes) - 1:
        size_in_bytes /= 1000
        i += 1

    if no_decimals:
        s = (
            "{:.{prec}f}".format(size_in_bytes, prec=no_decimals)
            .rstrip("0")
            .rstrip(".")
        )
    else:
        s = "{:.0f}".format(size_in_bytes)
    return s + " " + suffixes[i]


def divide_list(source: list, no_pieces: int) -> list:
    r"""
    Returns a list containing no_pieces lists, with the items
    of the original list evenly distributed
    :param source: the list to divide
    :param no_pieces: the nubmer of pieces the lists
    :return: the new list

    >>> divide_list(list(range(12)), 4)
    [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9, 10, 11]]
    >>> divide_list(list(range(11)), 4)
    [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9, 10]]
    """
    source_size = len(source)
    slice_size = source_size // no_pieces
    remainder = source_size % no_pieces
    result = []

    extra = 0
    for i in range(no_pieces):
        start = i * slice_size + extra
        source_slice = source[start : start + slice_size]
        if remainder:
            source_slice += [source[start + slice_size]]
            remainder -= 1
            extra += 1
        result.append(source_slice)
    return result


def divide_list_on_length(source: List, length: int) -> List:

    r"""
    Break a list into lists no longer than length.

    >>> l=list(range(11))
    >>> divide_list_on_length(l, 3)
    [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9, 10]]
    >>> l=list(range(12))
    >>> divide_list_on_length(l, 3)
    [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9, 10, 11]]
    """

    return [source[i : i + length] for i in range(0, len(source), length)]


def addPushButtonLabelSpacer(s: str) -> str:
    return " " + s


class GenerateRandomFileName:
    def __init__(self):
        # the characters used to generate temporary file names
        self.file_name_characters = list(string.ascii_letters + string.digits)

    def name(self, extension: str = None) -> str:
        """
        :param extension: if included, random file name will include the
         file extension
        :return: file name 5 characters long with or without extension
        """
        if extension is not None:
            return "{}.{}".format(
                "".join(random.sample(self.file_name_characters, 5)), extension
            )
        else:
            return "".join(random.sample(self.file_name_characters, 5))


TempDirs = namedtuple("TempDirs", "photo_temp_dir, video_temp_dir")
CacheDirs = namedtuple("CacheDirs", "photo_cache_dir, video_cache_dir")


def create_temp_dir(
    folder: Optional[str] = None,
    prefix: Optional[str] = None,
    force_no_prefix: bool = False,
    temp_dir_name: Optional[str] = None,
) -> str:
    """
    Creates a temporary director and logs errors
    :param folder: the folder in which the temporary directory should
     be created. If not specified, uses the tempfile.mkstemp default.
    :param prefix: any name the directory should start with. If None,
     default rpd-tmp will be used as prefix, unless force_no_prefix
     is True
    :param force_no_prefix: if True, a directory prefix will never
     be used
    :param temp_dir_name: if specified, create the temporary directory
     using this actual name. If it already exists, add a suffix.
    :return: full path of the temporary directory
    """

    if temp_dir_name:
        if folder is None:
            folder = tempfile.gettempdir()
        for i in range(10):
            if i == 0:
                path = os.path.join(folder, temp_dir_name)
            else:
                path = os.path.join(folder, "{}_{}".format(temp_dir_name, i))
            try:
                os.mkdir(path=path, mode=0o700)
            except FileExistsError:
                logging.warning("Failed to create temporary directory %s", path)
            else:
                break
        return path
    else:
        if prefix is None and not force_no_prefix:
            prefix = "rpd-tmp-"
        try:
            temp_dir = tempfile.mkdtemp(prefix=prefix, dir=folder)
        except OSError as inst:
            msg = "Failed to create temporary directory in %s: %s %s" % (
                folder,
                inst.errno,
                inst.strerror,
            )
            logging.critical(msg)
            temp_dir = None
    return temp_dir


def create_temp_dirs(
    photo_download_folder: str, video_download_folder: str
) -> TempDirs:
    """
    Create pair of temporary directories for photo and video download
    :param photo_download_folder: where photos will be downloaded to
    :param video_download_folder: where videos will be downloaded to
    :return: the directories
    """
    photo_temp_dir = video_temp_dir = None
    if photo_download_folder is not None:
        photo_temp_dir = create_temp_dir(photo_download_folder)
        logging.debug("Photo temporary directory: %s", photo_temp_dir)
    if video_download_folder is not None:
        video_temp_dir = create_temp_dir(video_download_folder)
        logging.debug("Video temporary directory: %s", video_temp_dir)
    return TempDirs(photo_temp_dir, video_temp_dir)


def same_device(file1: str, file2: str) -> bool:
    """
    Returns True if the files / directories are on the same device (partition).

    No error checking.

    :param file1: first file / directory to check
    :param file2: second file / directory to check
    :return: True if the same file system, else false
    """

    dev1 = os.stat(file1).st_dev
    dev2 = os.stat(file2).st_dev
    return dev1 == dev2


def find_mount_point(path: str) -> str:
    """
    Find the mount point of a path
    See:
    http://stackoverflow.com/questions/4453602/how-to-find-the-mountpoint-a-file-resides-on

    >>> print(find_mount_point('/crazy/path'))
    /

    :param path:
    :return:
    """
    path = os.path.realpath(path)
    while not os.path.ismount(path):
        path = os.path.dirname(path)
    return path


def make_internationalized_list(items: List[str]) -> str:
    r"""
    Makes a string of items conforming to i18n

    >>> print(make_internationalized_list([]))
    <BLANKLINE>
    >>> print(make_internationalized_list(['one']))
    one
    >>> print(make_internationalized_list(['one', 'two']))
    one and two
    >>> print(make_internationalized_list(['one', 'two', 'three']))
    one, two and three
    >>> print(make_internationalized_list(['one', 'two', 'three', 'four']))
    one, two, three and four

    Loosely follows the guideline here:
    http://cldr.unicode.org/translation/lists

    :param items: the list of items to make a string out of
    :return: internationalized string
    """

    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        # Translators: two things in a list e.g. "device1 and device2"
        # Translators: %(variable)s represents Python code, not a plural of the term
        # variable. You must keep the %(variable)s untranslated, or the program will
        # crash.
        return _("%(first_item)s and %(last_item)s") % dict(
            first_item=items[0], last_item=items[1]
        )
    if len(items) > 2:
        s = items[0]
        for item in items[1:-1]:
            # Translators: the middle of a list of things
            # Translators: %(variable)s represents Python code, not a plural of the term
            # variable. You must keep the %(variable)s untranslated, or the program will
            # crash.
            s = "%(first_items)s, %(last_items)s" % dict(first_items=s, last_items=item)
        # Translators: the end of a list of things
        # Translators: %(variable)s represents Python code, not a plural of the term
        # variable. You must keep the %(variable)s untranslated, or the program will
        # crash.
        s = "%(start_items)s and %(last_item)s" % dict(
            start_items=s, last_item=items[-1]
        )
        return s
    return ""


def thousands(i: int) -> str:
    """
    Add a thousands separator (or its locale equivalent) to an
    integer. Assumes the module level locale setting has already been
    set.
    :param i: the integer e.g. 1000
    :return: string with seperators e.g. '1,000'
    """
    try:
        return locale.format_string("%d", i, grouping=True)
    except TypeError:
        return str(i)


# Source of class AdjacentKey, first_and_last and runs:
# http://stupidpythonideas.blogspot.com/2014/01/grouping-into-runs-of-adjacent-values.html
class AdjacentKey:
    r"""
    >>> [list(g) for k, g in groupby([0, 1, 2, 3, 5, 6, 7, 10, 11, 13, 16], AdjacentKey)]
    [[0, 1, 2, 3], [5, 6, 7], [10, 11], [13], [16]]
    """
    __slots__ = ["obj"]

    def __init__(self, obj) -> None:
        self.obj = obj

    def __eq__(self, other) -> bool:
        ret = self.obj - 1 <= other.obj <= self.obj + 1
        if ret:
            self.obj = other.obj
        return ret


def first_and_last(iterable):
    start = end = next(iterable)
    for end in iterable:
        pass
    return start, end


def runs(iterable):
    r"""
    identify adjacent elements in pre-sorted data

    :param iterable: sorted data

    >>> list(runs([0, 1, 2, 3, 5, 6, 7, 10, 11, 13, 16]))
    [(0, 3), (5, 7), (10, 11), (13, 13), (16, 16)]
    >>> list(runs([0]))
    [(0, 0)]
    >>> list(runs([0, 1, 10, 100, 101]))
    [(0, 1), (10, 10), (100, 101)]
    """

    for k, g in groupby(iterable, AdjacentKey):
        yield first_and_last(g)


numbers = namedtuple("numbers", "number, plural")


long_numbers = {
    1: _("one"),
    2: _("two"),
    3: _("three"),
    4: _("four"),
    5: _("five"),
    6: _("six"),
    7: _("seven"),
    8: _("eight"),
    9: _("nine"),
    10: _("ten"),
    11: _("eleven"),
    12: _("twelve"),
    13: _("thirteen"),
    14: _("fourteen"),
    15: _("fifteen"),
    16: _("sixteen"),
    17: _("seventeen"),
    18: _("eighteen"),
    19: _("ninenteen"),
    20: _("twenty"),
}


def number(value: int) -> numbers:
    r"""
    Convert integer to written form, e.g. one, two, etc.

    Will propagate TypeError or KeyError on
    failure.

    >>> number(1)
    numbers(number='one', plural=False)
    >>> number(2)
    numbers(number='two', plural=True)
    >>> number(10)
    numbers(number='ten', plural=True)
    >>> number(20)
    numbers(number='twenty', plural=True)
    >>>

    :param value: int between 1 and 20
    :return: tuple of str and whether it is plural
    """

    plural = value > 1
    text = long_numbers[value]
    return numbers(text, plural)


def datetime_roughly_equal(
    dt1: Union[datetime, float], dt2: Union[datetime, float], seconds: int = 120
) -> bool:
    r"""
    Check to see if date times are equal, give or take n seconds
    :param dt1: python datetime, or timestamp, to check
    :param dt2:python datetime, or timestamp to check
    :param seconds: number of seconds leeway
    :return: True if "equal", False otherwise

    >>> dt1 = datetime.now()
    >>> time.sleep(.1)
    >>> dt2 = datetime.now()
    >>> datetime_roughly_equal(dt1, dt2, 1)
    True
    >>> dt1 = 1458561776.0
    >>> dt2 = 1458561776.0
    >>> datetime_roughly_equal(dt1, dt2, 120)
    True
    >>> dt2 += 450
    >>> datetime_roughly_equal(dt1, dt2, 120)
    False
    >>> datetime_roughly_equal(dt1, dt2, 500)
    True
    """

    # arrow.get from time stamp gives UTC time
    at1 = arrow.get(dt1)
    at2 = arrow.get(dt2)
    if arrow_shift_support:
        return at1.shift(seconds=-seconds) < at2 < at1.shift(seconds=+seconds)
    else:
        return at1.replace(seconds=-seconds) < at2 < at1.replace(seconds=+seconds)


def process_running(process_name: str, partial_name: bool = True) -> bool:
    """
    Search the list of the system's running processes to see if a process with this
    name is running

    :param process_name: the name of the process to search for
    :param partial_name: if True, the process_name argument can be a
     partial match
    :return: True if found, else False
    """

    for proc in psutil.process_iter():
        try:
            name = proc.name()
        except psutil.NoSuchProcess:
            pass
        else:
            if partial_name:
                if name.find(process_name) >= 0:
                    return True
            else:
                if name == process_name:
                    return True
    return False


def make_html_path_non_breaking(path: str) -> str:
    """
    When /some/path is displayed in rich text, it will be word-wrapped on the
    slashes. Inhibit that using a special unicode character.

    :param path: the path
    :return: the path containing the special characters
    """

    return path.replace(os.sep, "{}&#8288;".format(os.sep))


def prefs_list_from_gconftool2_string(value: str) -> List[str]:
    r"""
    Take a raw string preference value as returned by gconftool-2
    and convert it to a list of strings.

    Handles escaped characters

    :param value: the raw value as returned by gconftool-2
    :return: the list of strings

    >>> prefs_list_from_gconftool2_string( # doctest: +ELLIPSIS
    ... '[Text,IMG_,,Sequences,Stored number,Four digits,Filename,Extension,UPPERCASE]')
    ... # doctest: +NORMALIZE_WHITESPACE
    ['Text', 'IMG_', '', 'Sequences', 'Stored number', 'Four digits', 'Filename', 'Extension',
    'UPPERCASE']
    >>> prefs_list_from_gconftool2_string('[Text,IMG_\,\\;+=|!@\,#^&*()$%/",,]')
    ['Text', 'IMG_,\\;+=|!@,#^&*()$%/"', '', '']
    >>> prefs_list_from_gconftool2_string('[Manila,Dubai,London]')
    ['Manila', 'Dubai', 'London']
    """
    # Trim the left and right square brackets
    value = value[1:-1]

    # Split on the comma, but not commas that were escaped.
    # Use a regex with a negative lookbehind assertion
    splits = re.split(r"(?<!\\),", value)
    # Replace the escaped commas with just plain commas
    return [s.replace("\\,", ",") for s in splits]


def pref_bool_from_gconftool2_string(value: str) -> bool:
    if value == "true":
        return True
    elif value == "false":
        return False
    raise ValueError


def remove_last_char_from_list_str(items: List[str]) -> List[str]:
    r"""
    Remove the last character from a list of strings, modifying the list in place,
    such that the last item is never empty

    :param items: the list to modify
    :return: in place copy

    >>> remove_last_char_from_list_str([' abc', 'def', 'ghi'])
    [' abc', 'def', 'gh']
    >>> remove_last_char_from_list_str([' abc', 'def', 'gh'] )
    [' abc', 'def', 'g']
    >>> remove_last_char_from_list_str([' abc', 'def', 'g'] )
    [' abc', 'def']
    >>> remove_last_char_from_list_str([' a'])
    [' ']
    >>> remove_last_char_from_list_str([' '])
    []
    >>> remove_last_char_from_list_str([])
    []
    """
    if items:
        if not items[-1]:
            items = items[:-1]
        else:
            items[-1] = items[-1][:-1]
            if items and not items[-1]:
                items = items[:-1]
    return items


def platform_c_maxint() -> int:
    """
    See http://stackoverflow.com/questions/13795758/what-is-sys-maxint-in-python-3

    :return: the maximum size of an int in C when compiled the same way Python was
    """
    return 2 ** (struct.Struct("i").size * 8 - 1) - 1


def _recursive_identify_depth(*paths, depth) -> int:
    basenames = [os.path.basename(path) for path in paths]
    if len(basenames) != len(set(basenames)):
        duplicates = _collect_duplicates(basenames, paths)

        for basename in duplicates:
            chop = len(basename) + 1
            chopped = (path[:-chop] for path in duplicates[basename])
            depth = max(depth, _recursive_identify_depth(*chopped, depth=depth + 1))
    return depth


def _collect_duplicates(basenames, paths):
    duplicates = defaultdict(list)
    for basename, path in zip(basenames, paths):
        duplicates[basename].append(path)
    return {basename: paths for basename, paths in duplicates.items() if len(paths) > 1}


def make_path_end_snippets_unique(*paths) -> List[str]:
    r"""
    Make list of path ends unique given possible common path endings.

    A snippet starts from the end of the path, in extreme cases possibly up the path start.

    :param paths: sequence of paths to generate unique end snippets for
    :return: list of unique snippets

    >>> p0 = '/home/damon/photos'
    >>> p1 = '/media/damon/backup1/photos'
    >>> p2 = '/media/damon/backup2/photos'
    >>> p3 = '/home/damon/videos'
    >>> p4 = '/media/damon/backup1/videos'
    >>> p5 = '/media/damon/backup2/videos'
    >>> p6 = '/media/damon/drive1/home/damon/photos'
    >>> s0 = make_path_end_snippets_unique(p0, p3)
    >>> print(s0)
    ['photos', 'videos']
    >>> s1 = make_path_end_snippets_unique(p0, p1, p2)
    >>> print(s1)
    ['damon/photos', 'backup1/photos', 'backup2/photos']
    >>> s2 = make_path_end_snippets_unique(p0, p1, p2, p3)
    >>> print(s2)
    ['damon/photos', 'backup1/photos', 'backup2/photos', 'videos']
    >>> s3 = make_path_end_snippets_unique(p3, p4, p5)
    >>> print(s3)
    ['damon/videos', 'backup1/videos', 'backup2/videos']
    >>> s4 = make_path_end_snippets_unique(p0, p1, p2, p3, p6)
    >>> print(s4) #doctest: +NORMALIZE_WHITESPACE
    ['/home/damon/photos', '/media/damon/backup1/photos', '/media/damon/backup2/photos', 'videos',
     'drive1/home/damon/photos']
    >>> s5 = make_path_end_snippets_unique(p1, p2, p3, p6)
    >>> print(s5)
    ['backup1/photos', 'backup2/photos', 'videos', 'damon/photos']
    """

    basenames = [os.path.basename(path) for path in paths]

    if len(basenames) != len(set(basenames)):
        names = []
        depths = defaultdict(int)
        duplicates = _collect_duplicates(basenames, paths)

        for basename, path in zip(basenames, paths):
            if basename in duplicates:
                depths[basename] = _recursive_identify_depth(
                    *duplicates[basename], depth=0
                )

        for basename, path in zip(basenames, paths):
            depth = depths[basename]
            if depth:
                dirs = path.split(os.sep)
                index = len(dirs) - depth - 1
                name = os.sep.join(dirs[max(index, 0) :])
                if index > 1:
                    pass
                    # name = '...' + name
                elif index == 1:
                    name = os.sep + name
            else:
                name = basename
            names.append(name)
        return names
    else:
        return basenames


have_logged_os_release = False


def log_os_release() -> None:
    """
    Log the entired contents of /etc/os-release, but only if
    we didn't do so already.
    """

    global have_logged_os_release

    if not have_logged_os_release:
        try:
            with open("/etc/os-release", "r") as f:
                for line in f:
                    logging.debug(line.rstrip("\n"))
        except:
            pass
        have_logged_os_release = True


def extract_file_from_tar(full_tar_path, member_filename) -> bool:
    """
    Extracts a file from a tar.gz and places it beside the tar file
    :param full_tar_path: path and filename of the tar.gz file
    :param member_filename: file wanted
    :return: True if successful, False otherwise
    """

    tar_dir, tar_name = os.path.split(full_tar_path)
    tar_name = tar_name[: len(".tar.gz") * -1]
    member = os.path.join(tar_name, member_filename)
    try:
        with tarfile.open(full_tar_path) as tar:
            tar.extractall(members=(tar.getmember(member),), path=tar_dir)
    except Exception:
        logging.error("Unable to extract %s from tarfile", member_filename)
        return False
    else:
        try:
            src = os.path.join(tar_dir, tar_name, member_filename)
            dst = os.path.join(tar_dir, member_filename)
            os.rename(src, dst)
            os.rmdir(os.path.join(tar_dir, tar_name))
            return True
        except OSError:
            logging.error("Unable to move %s to new location", member_filename)
            return False


def bug_report_full_tar_path() -> str:
    """
    Generate a full path for a compressed bug report tar file.
    The filename will not already exist.

    :return: File name including path
    """

    filename = "rpd-bug-report-{}".format(datetime.now().strftime("%Y%m%d"))
    component = os.path.join(os.path.expanduser("~"), filename)

    i = 0
    while os.path.isfile(
        "{}{}.tar.gz".format(component, "" if not i else "-{}".format(i))
    ):
        i += 1

    return "{}{}.tar.gz".format(component, "" if not i else "-{}".format(i))


def create_bugreport_tar(
    full_tar_name: str,
    log_path: Optional[str] = "",
    full_config_file: Optional[str] = "",
) -> bool:
    """
    Create a tar file containing log and configuration files.

    If the file already exists, do nothing.

    :param full_tar_name: the full path in which to create the tar file
    :param log_path: path to the log files
    :param full_config_file: the full path and file of the configuration file
    :return: True if tar file created, else False
    """

    if os.path.isfile(full_tar_name):
        logging.error("Cannot create bug report tarfile, because it already exists")
        return False

    if not log_path:
        log_path = os.path.join(
            QStandardPaths.writableLocation(QStandardPaths.GenericCacheLocation),
            "rapid-photo-downloader",
            "log",
        )

    if not full_config_file:
        config_dir = os.path.join(
            QStandardPaths.writableLocation(QStandardPaths.GenericConfigLocation),
            "Rapid Photo Downloader",
        )
        config_file = "Rapid Photo Downloader.conf"
    else:
        config_dir, config_file = os.path.split(full_config_file)

    curr_dir = os.getcwd()
    created = False

    try:
        with tarfile.open(full_tar_name, "x:gz") as t:
            os.chdir(log_path)
            for l in glob("*"):
                t.add(
                    l,
                    "rapid-photo-downloader.0.log"
                    if l == "rapid-photo-downloader.log"
                    else l,
                )
            os.chdir(config_dir)
            t.add(config_file)
    except FileNotFoundError as e:
        logging.error(
            "When creating a bug report tar file, the directory or file %s does not exist",
            e.filename,
        )
    except Exception:
        logging.exception("Unexpected error when creating bug report tar file")
    else:
        created = True

    try:
        os.chdir(curr_dir)
    except FileNotFoundError:
        pass

    return created


def current_version_is_dev_version(current_version=None) -> bool:
    if current_version is None:
        current_version = parse_version(__about__.__version__)
    return current_version.is_prerelease


def remove_topmost_directory_from_path(path: str) -> str:
    if os.sep not in path:
        return path
    return path[path[1:].find(os.sep) + 1 :]


def arrow_locale(lang: str) -> str:
    """
    Test if locale is suitable for use with Arrow.
    :return: Return user locale if it works with Arrow, else Arrow default ('en_us')
    """

    default = "en_us"
    if not lang:
        try:
            lang = locale.getdefaultlocale()[0]
        except Exception:
            return default

    try:
        arrow.locales.get_locale(lang)
        return lang
    except (ValueError, AttributeError):
        return default


def letters(x: int) -> str:
    """
    Return a letter representation of a positive number.

    Adapted from algorithm at
    http://en.wikipedia.org/wiki/Hexavigesimal

    >>> letters(0)
    'a'
    >>> letters(1)
    'b'
    >>> letters(2)
    'c'
    >>> letters(25)
    'z'
    >>> letters(26)
    'aa'
    >>> letters(27)
    'ab'
    >>> letters(28)
    'ac'
    """

    v = ""
    while x > 25:
        r = x % 26
        x = x // 26 - 1
        v = string.ascii_lowercase[r] + v
    v = string.ascii_lowercase[x] + v

    return v


# Use to extract time zone information from date / times:
_flexible_dt_re = re.compile(
    r"""(?P<year>\d{4})[:-](?P<month>\d{2})[:-](?P<day>\d{2})
        [\sT]  # separator between date and time
        (?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})
        (?P<subsecond>\.\d{2})?
        (?P<timezone>([+-])\d{2}:\d{2})?
        (?P<dst>\s(DST))?""",
    re.VERBOSE,
)


def flexible_date_time_parser(dt_string: str) -> Tuple[datetime, str]:
    r"""
    Use regular expresion to parse exif date time value, and attempt
    to convert it to a python date time.
    No error checking.

    :param dt_string: date time from exif in string format
    :return: datetime, may or may not have a time zone, and format string

    >>> flexible_date_time_parser('2018:09:03 14:00:13+01:00 DST')
    datetime.datetime(2018, 9, 3, 14, 0, 13, tzinfo=datetime.timezone(datetime.timedelta(0, 3600)))
    >>> flexible_date_time_parser('2010:07:18 01:53:35')
    datetime.datetime(2010, 7, 18, 1, 53, 35)
    >>> flexible_date_time_parser('2016:02:27 22:18:03.00')
    datetime.datetime(2016, 2, 27, 22, 18, 3)
    >>> flexible_date_time_parser('2010:05:25 17:43:16+02:00')
    datetime.datetime(2010, 5, 25, 17, 43, 16, tzinfo=datetime.timezone(datetime.timedelta(0, 7200)))
    >>> flexible_date_time_parser('2010:06:07 14:14:02+00:00')
    datetime.datetime(2010, 6, 7, 14, 14, 2, tzinfo=datetime.timezone.utc)
    >>> flexible_date_time_parser('2016-11-25T14:31:24')
    datetime.datetime(2016, 11, 25, 14, 31, 24)
    >>> flexible_date_time_parser('2016-11-25T14:20:09')
    datetime.datetime(2016, 11, 25, 14, 20, 9)
    """

    match = _flexible_dt_re.match(dt_string)
    assert match
    m = match.groupdict()

    dte = "{}:{}:{} {}:{}:{}".format(
        m["year"], m["month"], m["day"], m["hour"], m["minute"], m["second"]
    )

    fs = "%Y:%m:%d %H:%M:%S"  # format string

    ss = m["subsecond"]
    if ss:
        dte = "{}{}".format(dte, ss)
        fs = "{}.%f".format(fs)

    tze = m["timezone"]
    if tze:
        dte = "{}{}".format(dte, tze.replace(":", ""))
        fs = "{}%z".format(fs)

    # dst: daylight savings
    # no idea how to handle this properly -- so ignore for now!

    return datetime.strptime(dte, fs), fs


def image_large_enough_fdo(size: QSize) -> bool:
    """
    :param size: image size
    :return: True if image is large enough to meet the FreeDesktop.org
     specs for a large thumbnail
    """

    return size.width() >= 256 or size.height() >= 256


def is_venv():
    """
    :return: True if the python interpreter is running in venv or virtualenv
    """

    return hasattr(sys, "real_prefix") or (
        hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
    )


def python_package_version(package: str) -> str:
    """
    Determine the version of an installed Python package
    :param package: package name
    :return: version number, if could be determined, else ''
    """

    try:
        return pkg_resources.get_distribution(package).version
    except pkg_resources.DistributionNotFound:
        return ""


def is_snap() -> bool:
    """
    Determine if program is running in a snap environment
    :return: True if it is False otherwise
    """

    snap_name = os.getenv("SNAP_NAME", "")
    return snap_name.find("rapid-photo-downloader") >= 0


def version_check_disabled():
    """
    Determine if version checking should be disabled or not
    :return: True if it should be False otherwise
    """

    return disable_version_check or is_snap()


def available_lang_codes() -> List[str]:
    """
    Detect translations that exist for Rapid Photo Downloader
    :return: list of language codes
    """

    if localedir is not None:
        files = glob(os.path.join(localedir, "*", "LC_MESSAGES", "%s.mo" % i18n_domain))
        langs = [file.split(os.path.sep)[-3] for file in files]
        langs.append("en")
        return langs
    else:
        return []


# Auto-generated from extract_language_names.py do not delete
substitute_languages = {
    "fa": "Persian",
    "sk": "Slovak",
    "it": "Italian",
    "oc": "Occitan (post 1500)",
    "fi": "Finnish",
    "sv": "Swedish",
    "cs": "Czech",
    "pl": "Polish",
    "kab": "Kabyle",
    "tr": "Turkish",
    "hr": "Croatian",
    "nn": "Norwegian Nynorsk",
    "da": "Danish",
    "de": "German",
    "sr": "српски",
    "pt_BR": "Brazilian Portuguese",
    "ja": "Japanese",
    "bg": "Bulgarian",
    "uk": "Ukrainian",
    "ar": "Arabic",
    "ca": "Catalan",
    "nb": "Norwegian Bokmal",
    "ru": "Russian",
    "hu": "magyar",
    "be": "Belarusian",
    "es": "Spanish",
    "pt": "Portuguese",
    "zh_CN": "Chinese (Simplified)",
    "fr": "Français",
    "et": "Estonian",
    "nl": "Dutch",
    "ro": "Romanian",
    "id": "Indonesian",
    "el": "Greek",
}  # Auto-generated from extract_language_names.py do not delete


def get_language_display_name(
    lang_code: str, make_missing_lower: bool, locale_code: str
) -> str:
    """
    Use babel to the human friendly name for a locale, or failing that our
    auto-generated version
    :param lang_code: locale code for language to get the display name for
    :param make_missing_lower: whether to make the default name when
     babel does not suppply it lower case
    :param locale_code: current system locale code
    :return: human friendly version
    """

    try:
        return babel.Locale.parse(lang_code).get_display_name(locale_code)
    except babel.core.UnknownLocaleError:
        display = substitute_languages[lang_code]
        return display if not make_missing_lower else display.lower()


def available_languages(display_locale_code: str = "") -> List[Tuple[str, str]]:
    """
    Detect translations that exist for Rapid Photo Downloader
    :return: iterator of Tuple of language code and localized name
    """

    lang_codes = available_lang_codes()

    if not lang_codes:  # Testing code when translations are not installed
        lang_codes = ["en", "de", "es"]

    if not display_locale_code:
        try:
            locale_code = locale.getdefaultlocale()[0]
        except Exception:
            locale_code = "en_US"
    else:
        locale_code = display_locale_code

    # Determine if this locale makes its language names lower case
    babel_sample = babel.Locale.parse("en").get_display_name(locale_code)
    make_missing_lower = babel_sample.islower()

    langs = zip(
        lang_codes,
        [
            get_language_display_name(code, make_missing_lower, locale_code)
            for code in lang_codes
        ],
    )

    # Sort languages by display name
    langs = list(langs)
    try:
        langs.sort(key=lambda l: locale.strxfrm(l[1]))
    except Exception:
        logging.error("Error sorting language names for display in program preferences")
    return langs


def installed_using_pip(package: str, suppress_errors: bool = True) -> bool:
    """
    Determine if python package was installed in local directory using pip.

    Determination is not 100% robust in all circumstances.

    :param package: package name to search for
    :param suppress_errors: if True, silently catch all exceptions and return False
    :return: True if installed via pip, else False
    """

    # pkg_resources.get_distribution(package).get_metadata('INSTALLER') output seems
    # to be unreliable
    # newer way is importlib.metadata.distribution('pip').read_text('INSTALLER') but
    # it has the same problem

    try:
        pkg = pkg_resources.get_distribution(package)
        location = pkg.location
        return not location.startswith("/usr") or location.find("local") > 0
    except Exception:
        if not suppress_errors:
            raise
        return False


def getQtSystemTranslation(locale_name: str) -> Optional[QTranslator]:
    """
    Attempt to install Qt base system translations (for QMessageBox and QDialogBox buttons)
    :return: translator if loaded, else None
    """

    # These locales are found in the path QLibraryInfo.TranslationsPath
    convert_locale = dict(
        cs_CZ="cs",
        da_DK="da",
        de_DE="de",
        es_ES="es",
        fi_FI="fi",
        fr_FR="fr",
        it_IT="it",
        ja_JP="ja",
        hu_HU="hu",
        pl_PL="pl",
        ru_RU="ru",
        sk_SK="sk",
        uk_UA="uk",
    )

    qtTranslator = QTranslator()
    location = QLibraryInfo.location(QLibraryInfo.TranslationsPath)
    qm_file = "qtbase_{}.qm".format(convert_locale.get(locale_name, locale_name))
    qm_file = os.path.join(location, qm_file)
    if os.path.isfile(qm_file):
        if qtTranslator.load(qm_file):
            logging.debug("Installing Qt support for locale %s", locale_name)
            return qtTranslator
        else:
            logging.debug("Could not load Qt locale file %s", qm_file)


def existing_parent_for_new_dir(path: Path) -> Path:
    """
    Locate the first parent folder that exists for a given path
    :param path: path to look for first existing parent
    :return: the first parent folder that exists for the  path
    """
    for parent in path.parents:
        if parent.is_dir():
            return parent
