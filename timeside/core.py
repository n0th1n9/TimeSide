# -*- coding: utf-8 -*-
#
# Copyright (c) 2009-2013 Parisson SARL
# Copyright (c) 2009 Olivier Guilyardi <olivier@samalyse.com>
#
# This file is part of TimeSide.

# TimeSide is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.

# TimeSide is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with TimeSide.  If not, see <http://www.gnu.org/licenses/>.

from timeside.component import *
from timeside.api import IProcessor
from timeside.exceptions import Error, ApiError


import re
import time
import numpy
import uuid

__all__ = ['Processor', 'MetaProcessor', 'implements', 'abstract',
           'interfacedoc', 'processors', 'get_processor', 'ProcessPipe',
           'FixedSizeInputAdapter']

_processors = {}


class MetaProcessor(MetaComponent):
    """Metaclass of the Processor class, used mainly for ensuring that processor
    id's are wellformed and unique"""

    valid_id = re.compile("^[a-z][_a-z0-9]*$")

    def __new__(cls, name, bases, d):
        new_class = MetaComponent.__new__(cls, name, bases, d)
        if new_class in implementations(IProcessor):
            id = str(new_class.id())
            if _processors.has_key(id):
                # Doctest test can duplicate a processor
                # This can be identify by the conditon "module == '__main__'"
                if new_class.__module__ == '__main__':
                    new_class = _processors[id]
                elif _processors[id].__module__ == '__main__':
                    pass
                else:
                    raise ApiError("%s and %s have the same id: '%s'"
                        % (new_class.__name__, _processors[id].__name__, id))
            if not MetaProcessor.valid_id.match(id):
                raise ApiError("%s has a malformed id: '%s'"
                    % (new_class.__name__, id))

            _processors[id] = new_class

        return new_class


class Processor(Component):
    """Base component class of all processors


    Attributes:
              parents :  List of parent Processors that must be processed
                         before the current Processor
              pipe :     The current ProcessPipe in which the Processor will run
        """
    __metaclass__ = MetaProcessor

    abstract()
    implements(IProcessor)

    def __init__(self):
        super(Processor, self).__init__()

        self.parents = []
        self.source_mediainfo = None
        self.pipe = None
        self.UUID = uuid.uuid4()

    @interfacedoc
    def setup(self, channels=None, samplerate=None, blocksize=None,
              totalframes=None):
        self.source_channels     = channels
        self.source_samplerate   = samplerate
        self.source_blocksize    = blocksize
        self.source_totalframes  = totalframes

        # If empty Set default values for input_* attributes
        # may be setted by the processor during __init__()
        if not hasattr(self, 'input_channels'):
            self.input_channels = self.source_channels
        if not hasattr(self, 'input_samplerate'):
            self.input_samplerate = self.source_samplerate
        if not hasattr(self, 'input_blocksize'):
            self.input_blocksize = self.source_blocksize
        if not hasattr(self, 'input_stepsize'):
            self.input_stepsize = self.source_blocksize


    # default channels(), samplerate() and blocksize() implementations returns
    # the source characteristics, but processors may change this behaviour by
    # overloading those methods
    @interfacedoc
    def channels(self):
        return self.source_channels

    @interfacedoc
    def samplerate(self):
        return self.source_samplerate

    @interfacedoc
    def blocksize(self):
        return self.source_blocksize

    @interfacedoc
    def totalframes(self):
        return self.source_totalframes

    @interfacedoc
    def process(self, frames, eod):
        return frames, eod

    @interfacedoc
    def post_process(self):
        pass

    @interfacedoc
    def release(self):
        pass

    @interfacedoc
    def mediainfo(self):
        return self.source_mediainfo

    @interfacedoc
    def uuid(self):
        return str(self.UUID)

    def __del__(self):
        self.release()

    def __or__(self, other):
        return ProcessPipe(self, other)


class FixedSizeInputAdapter(object):
    """Utility to make it easier to write processors which require fixed-sized
    input buffers."""

    def __init__(self, buffer_size, channels, pad=False):
        """Construct a new adapter: buffer_size is the desired buffer size in frames,
        channels the number of channels, and pad indicates whether the last block should
        be padded with zeros."""

        self.buffer      = numpy.empty((buffer_size, channels))
        self.buffer_size = buffer_size
        self.len         = 0
        self.pad         = pad

    def blocksize(self, input_totalframes):
        """Return the total number of frames that this adapter will output according to the
        input_totalframes argument"""

        blocksize = input_totalframes
        if self.pad:
            mod = input_totalframes % self.buffer_size
            if mod:
                blocksize += self.buffer_size - mod

        return blocksize

    def process(self, frames, eod):
        """Returns an iterator over tuples of the form (buffer, eod) where buffer is a
        fixed-sized block of data, and eod indicates whether this is the last block.
        In case padding is deactivated the last block may be smaller than the buffer size.
        """
        src_index = 0
        remaining = len(frames)

        while remaining:
            space   = self.buffer_size - self.len
            copylen = remaining < space and remaining or space
            src     = frames[src_index:src_index + copylen]
            if self.len == 0 and copylen == self.buffer_size:
                # avoid unnecessary copy
                buffer = src
            else:
                buffer = self.buffer
                buffer[self.len:self.len + copylen] = src

            remaining -= copylen
            src_index += copylen
            self.len  += copylen

            if self.len == self.buffer_size:
                yield buffer, (eod and not remaining)
                self.len = 0

        if eod and self.len:
            block = self.buffer
            if self.pad:
                self.buffer[self.len:self.buffer_size] = 0
            else:
                block = self.buffer[0:self.len]

            yield block, True
            self.len = 0


def processors(interface=IProcessor, recurse=True):
    """Returns the processors implementing a given interface and, if recurse,
    any of the descendants of this interface."""
    return implementations(interface, recurse)


def get_processor(processor_id):
    """Return a processor by its id"""
    if not _processors.has_key(processor_id):
        raise Error("No processor registered with id: '%s'"
                      % processor_id)

    return _processors[processor_id]


class ProcessPipe(object):
    """Handle a pipe of processors

    Attributes:
        processor: List of all processors in the Process pipe
        results : Results Container for all the analyzers of the Pipe process
"""

    def __init__(self, *others):
        self.processors = []
        self |= others

        from timeside.analyzer.core import AnalyzerResultContainer
        self.results = AnalyzerResultContainer()

    def __or__(self, other):
        return ProcessPipe(self, other)

    def __ior__(self, other):
        if isinstance(other, Processor):
            for parent in other.parents:
                self |= parent
            self.processors.append(other)
            other.process_pipe = self
        elif isinstance(other, ProcessPipe):
            self.processors.extend(other.processors)
        else:
            try:
                iter(other)
            except TypeError:
                raise Error("Can not add this type of object to a pipe: %s", str(other))

            for item in other:
                self |= item

        return self

    def __repr__(self):
        pipe = ''
        for item in self.processors:
            pipe += item.id()
            if item != self.processors[-1]:
                pipe += ' | '
        return pipe

    def run(self, channels=None, samplerate=None, blocksize=None, stack=None):
        """Setup/reset all processors in cascade and stream audio data along
        the pipe. Also returns the pipe itself."""

        source = self.processors[0]
        items = self.processors[1:]
        source.setup(channels=channels, samplerate=samplerate,
                     blocksize=blocksize)

        if stack is None:
                self.stack = False
        else:
            self.stack = stack

        if self.stack:
            self.frames_stack = []

        last = source

        # setup/reset processors and configure properties throughout the pipe
        for item in items:
            item.source_mediainfo = source.mediainfo()
            item.setup(channels=last.channels(),
                       samplerate=last.samplerate(),
                       blocksize=last.blocksize(),
                       totalframes=last.totalframes())
            last = item

        # now stream audio data along the pipe
        eod = False
        while not eod:
            frames, eod = source.process()
            if self.stack:
                self.frames_stack.append(frames)
            for item in items:
                frames, eod = item.process(frames, eod)

        # Post-processing
        for item in items:
            item.post_process()

        # Release processors
        if self.stack:
            if not isinstance(self.frames_stack, numpy.ndarray):
                self.frames_stack = numpy.vstack(self.frames_stack)
            from timeside.decoder.core import ArrayDecoder
            new_source = ArrayDecoder(samples=self.frames_stack,
                                      samplerate=source.samplerate())
            new_source.setup(channels=source.channels(),
                             samplerate=source.samplerate(),
                             blocksize=source.blocksize())
            self.processors[0] = new_source

        for item in items:
            item.release()
            self.processors.remove(item)
