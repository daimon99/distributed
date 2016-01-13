""" This file is experimental and may disappear without warning """
from __future__ import print_function, division, absolute_import

import logging
from math import log
import os

from dask.imperative import Value
from toolz import merge

from .executor import default_executor
from .utils import ignoring


logger = logging.getLogger(__name__)


def get_block_locations(hdfs, filename):
    """ Get block locations from a filename or globstring """
    return [merge({'filename': fn}, block)
            for fn in hdfs.glob(filename)
            for block in hdfs.get_block_locations(fn)]


def read_binary(fn, executor=None, hdfs=None, lazy=False, delimiter=None, **hdfs_auth):
    """ Convert location in HDFS to a list of distributed futures

    Parameters
    ----------
    fn: string
        location in HDFS
    executor: Executor (optional)
        defaults to most recently created executor
    hdfs: HDFileSystem
    **hdfs_auth: keyword arguments
        Extra keywords to send to ``hdfs3.HDFileSystem``

    Returns
    -------
    List of ``distributed.Future`` objects
    """
    from hdfs3 import HDFileSystem
    hdfs = hdfs or HDFileSystem(**hdfs_auth)
    executor = default_executor(executor)
    blocks = get_block_locations(hdfs, fn)
    filenames = [d['filename'] for d in blocks]
    offsets = [d['offset'] for d in blocks]
    lengths = [d['length'] for d in blocks]
    workers = [d['hosts'] for d in blocks]
    names = ['read-binary-%s-%d-%d' % (fn, offset, length)
            for fn, offset, length in zip(filenames, offsets, lengths)]


    logger.debug("Read %d blocks of binary bytes from %s", len(blocks), fn)
    if lazy:
        restrictions = dict(zip(names, workers))
        executor._send_to_scheduler({'op': 'update-graph',
                                    'dsk': {},
                                    'keys': [],
                                    'restrictions': restrictions,
                                    'loose_restrictions': set(names)})
        values = [Value(name, [{name: (hdfs.read_block, fn, offset, length, delimiter)}])
                  for name, fn, offset, length in zip(names, filenames, offsets, lengths)]
        return values
    else:
        return executor.map(hdfs.read_block, filenames, offsets, lengths,
                            delimiter=delimiter, workers=workers, allow_other_workers=True)


def write(fn, data, hdfs=None):
    """ Write bytes to HDFS """
    if not isinstance(data, bytes):
        raise TypeError("Data to write to HDFS must be of type bytes, got %s" %
                        type(data).__name__)
    with hdfs.open(fn, 'w') as f:
        f.write(data)
    return len(data)


def write_binary(path, futures, executor=None, hdfs=None, **hdfs_auth):
    """ Write bytestring futures to HDFS

    Parameters
    ----------
    path: string
        Path on HDFS to write data.  Either globstring like ``/data/file.*.dat``
        or a directory name like ``/data`` (directory will be created)
    futures: list
        List of futures.  Each future should refer to a block of bytes.
    executor: Executor
    hdfs: HDFileSystem

    Returns
    -------
    Futures that wait until writing is complete.  Returns the number of bytes
    written.

    Examples
    --------

    >>> write_binary('/data/file.*.dat', futures, hdfs=hdfs)  # doctest: +SKIP
    >>> write_binary('/data/', futures, hdfs=hdfs)  # doctest: +SKIP
    """
    from hdfs3 import HDFileSystem
    hdfs = hdfs or HDFileSystem(**hdfs_auth)
    executor = default_executor(executor)

    n = len(futures)
    n_digits = int(log(n) / log(10))
    template = '%0' + str(n_digits) + 'd'

    if '*' in path:
        dirname = os.path.split(path)[0]
        hdfs.mkdir(dirname)
        filenames = [path.replace('*', template % i) for i in range(n)]
    else:
        hdfs.mkdir(path)
        filenames = [os.path.join(path, template % i) for i in range(n)]

    return executor.map(write, filenames, futures, hdfs=hdfs)
