from itertools import product

from sympy import S
import numpy as np

from devito.dimension import DefaultDimension, IncrDimension
from devito.distributed import LEFT, RIGHT
from devito.ir.equations import DummyEq
from devito.ir.iet.nodes import (ArrayCast, Callable, Conditional, Expression,
                                 Iteration, List)
from devito.ir.iet.utils import derive_parameters
from devito.types import Array, Scalar
from devito.tools import is_integer

__all__ = ['copy', 'halo_exchange']


def copy(src, fixed):
    """
    Construct a :class:`Callable` copying an arbitrary convex region of ``src``
    into a contiguous :class:`Array`.
    """
    src_indices = []
    dst_indices = []
    dst_shape = []
    dst_dimensions = []
    for d in src.dimensions:
        dst_d = IncrDimension(d, S.Zero, S.One, name='dst_%s' % d)
        dst_dimensions.append(dst_d)
        if d in fixed:
            src_indices.append(fixed[d])
            dst_indices.append(0)
            dst_shape.append(1)
        else:
            src_indices.append(d + Scalar(name='o%s' % d, dtype=np.int32))
            dst_indices.append(dst_d)
            dst_shape.append(dst_d)
    dst = Array(name='dst', shape=dst_shape, dimensions=dst_dimensions)

    iet = Expression(DummyEq(dst[dst_indices], src[src_indices]))
    for sd, dd, s in reversed(list(zip(src.dimensions, dst.dimensions, dst.shape))):
        if is_integer(s):
            continue
        iet = Iteration(iet, sd, s.symbolic_size, uindices=dd)
    iet = List(body=[ArrayCast(src), ArrayCast(dst), iet])
    parameters = derive_parameters(iet)
    return Callable('copy', iet, 'void', parameters, ('static',))


def halo_exchange(f, fixed):
    """
    Construct an IET performing a halo exchange for a :class:`TensorFunction`.
    """
    assert f.is_Function

    # Compute send/recv array buffers
    buffers = {}
    for d0, i in product(f.dimensions, [LEFT, RIGHT]):
        if d0 in fixed:
            continue
        dimensions = [DefaultDimension(name='b', default_value=2)]
        halo = [(0, 0)]
        offsets = []
        for d1 in f.dimensions:
            if d1 in fixed:
                dimensions.append(DefaultDimension(name='h%s' % d1, default_value=1))
                halo.append((0, 0))
                offsets.append(fixed[d1])
            elif d0 is d1:
                if i is LEFT:
                    # TODO : probably need to swap left with right !! 
                    # As the stencils may be asymmetric ......
                    size = f._extent_halo[d0].left
                    offset = f._offset_halo[d0].left
                else:
                    size = f._extent_halo[d0].right
                    offset = f._offset_domain[d0].left + d0.symbolic_size
                dimensions.append(DefaultDimension(name='h%s' % d1, default_value=size))
                halo.append((0, 0))
                offsets.append(offset)
            else:
                dimensions.append(d1)
                halo.append(f._extent_halo[d0])
                offsets.append(0)
        name = 'B%s%s' % (d0, i.name[0])
        buffers[(d0, i)] = (Array(name=name, dimensions=dimensions, halo=halo), offsets)
    from IPython import embed; embed()

    for d in f.dimensions:
        for i in [LEFT, RIGHT]:

            mask = Scalar(name='m_%s%s' % (d, i.name[0]), dtype=np.int32)
            cond = Conditional(mask, ...)
