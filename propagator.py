import cgen_wrapper as cgen
from codeprinter import ccode
from sympy import symbols, IndexedBase, Indexed
from function_manager import FunctionDescriptor


class Propagator(object):
    def __init__(self, name, nt, shape, spc_border=0, forward=True, time_order=0):
        num_spac_dim = len(shape)
        self.t = symbols("t")
        space_dims = symbols("x y z")
        self.loop_counters = symbols("i1 i2 i3 i4")
        self._pre_kernel_steps = []
        self._post_kernel_steps = []
        self._forward = forward
        self.space_dims = space_dims[0:num_spac_dim]
        self.prep_variable_map()
        self.t_replace = {}
        self.time_steppers = []
        self.time_order = time_order
        self.nt = nt
        # Start with the assumption that the propagator needs to save the field in memory at every time step
        self._save = True
        # This might be changed later when parameters are being set

        # Which function parameters need special (non-save) time stepping and which don't
        self.save_vars = {}
        self.fd = FunctionDescriptor(name)
        if forward:
            self._time_step = 1
        else:
            self._time_step = -1
        self._space_loop_limits = {}
        for i, dim in enumerate(reversed(self.space_dims)):
                self._space_loop_limits[dim] = (spc_border, shape[i]-spc_border)

    @property
    def save(self):
        return self._save

    @save.setter
    def save(self, save):
        if save is not True:
            self.time_steppers = [symbols("t%d" % i) for i in range(self.time_order+1)]
            if self._forward is not True:
                self.t_replace = {(self.t): self.time_steppers[2], (self.t+1): self.time_steppers[1], (self.t+2): self.time_steppers[0]}
            else:
                self.t_replace = {(self.t): self.time_steppers[2], (self.t-1): self.time_steppers[1], (self.t-2): self.time_steppers[0]}
        self._save = self._save and save

    @property
    def time_loop_limits(self):
        num_save_vars = sum([1 for x in self.save_vars.values() if x is True])
        skip_time = self.time_order if num_save_vars > 0 else 0
        if self._forward:
            loop_limits = (0+skip_time, self.nt+skip_time)
        else:
            loop_limits = (self.nt-1, -1)
        return loop_limits

    def prep_variable_map(self):
        """ Mapping from model variables (x, y, z, t) to loop variables (i1, i2, i3, i4) - Needs work
        """
        var_map = {}
        i = 0
        for dim in self.space_dims:
            var_map[dim] = symbols("i%d" % (i + 1))
            i += 1
        var_map[self.t] = symbols("i%d" % (i + 1))
        self._var_map = var_map

    def prepare(self, subs, stencils, stencil_args):
        stmts = []
        for equality, args in zip(stencils, stencil_args):
            equality = equality.subs(dict(zip(subs, args)))
            equality = self.time_substitutions(equality)
            equality = equality.xreplace(self._var_map)
            stencil = cgen.Assign(ccode(equality.lhs), ccode(equality.rhs))
            stmts.append(stencil)
        kernel = self._pre_kernel_steps
        kernel += stmts
        kernel += self._post_kernel_steps
        return self.prepare_loop(cgen.Block(kernel))

    def prepare_loop(self, loop_body):
        num_spac_dim = len(self.space_dims)
        for dim_ind in range(1, num_spac_dim+1):
            dim_var = "i"+str(dim_ind)
            loop_limits = self._space_loop_limits[self.space_dims[dim_ind-1]]
            loop_body = cgen.For(cgen.InlineInitializer(cgen.Value("int", dim_var),
                                                        str(loop_limits[0])),
                                 dim_var + "<" + str(loop_limits[1]), dim_var + "++", loop_body)
        t_loop_limits = self.time_loop_limits
        t_var = str(self._var_map[self.t])
        cond_op = "<" if self._forward else ">"
        if self.save is not True:
            time_stepping = self.get_time_stepping()
        else:
            time_stepping = []
        loop_body = cgen.Block(time_stepping + [loop_body])
        loop_body = cgen.For(cgen.InlineInitializer(cgen.Value("int", t_var), str(t_loop_limits[0])), t_var + cond_op + str(t_loop_limits[1]), t_var + "+=" + str(self._time_step), loop_body)
        def_time_step = [cgen.Value("int", t_var_def.name) for t_var_def in self.time_steppers]
        body = def_time_step + [loop_body]
        return cgen.Block(body)

    def add_loop_step(self, sympy_condition, true_assign, false_assign=None, before=False):
        condition = ccode(sympy_condition.lhs.xreplace(self._var_map)) + " == " + ccode(sympy_condition.rhs.xreplace(self._var_map))
        true_str = cgen.Assign(ccode(self.time_substitutions(true_assign.lhs).xreplace(self._var_map)), ccode(self.time_substitutions(true_assign.rhs).xreplace(self._var_map)))
        false_str = cgen.Assign(ccode(self.time_substitutions(false_assign.lhs).xreplace(self._var_map)), ccode(self.time_substitutions(false_assign.rhs).xreplace(self._var_map))) if false_assign is not None else None
        statement = cgen.If(condition, true_str, false_str)
        if before:
            self._pre_kernel_steps.append(statement)
        else:
            self._post_kernel_steps.append(statement)

    def set_jit_params(self, subs, stencils, stencil_args):
        self.subs = subs
        self.stencils = stencils
        self.stencil_args = stencil_args

    def set_jit_simple(self, loop_body):
        self.loop_body = loop_body

    def add_param(self, name, shape, dtype, save=True):
        self.fd.add_matrix_param(name, shape, dtype)
        self.save = save
        self.save_vars[name] = save
        return IndexedBase(name)

    def add_scalar_param(self, name, dtype):
        self.fd.add_value_param(name, dtype)
        return symbols(name)

    def add_local_var(self, name, dtype):
        self.fd.add_local_variable(name, dtype)
        return symbols(name)

    def get_fd(self):
        """Get a FunctionDescriptor that describes the code represented by this Propagator
        in the format that FunctionManager and JitManager can deal with it. Before calling,
        make sure you have either called set_jit_params or set_jit_simple already.
        """
        try:  # Assume we have been given a a loop body in cgen types
            self.fd.set_body(self.prepare_loop(self.loop_body))
        except:  # We might have been given Sympy expression to evaluate
            # This is the more common use case so this will show up in error messages
            self.fd.set_body(self.prepare(self.subs, self.stencils, self.stencil_args))
        return self.fd

    def get_time_stepping(self):
        ti = self._var_map[self.t]
        body = []
        time_stepper_indices = range(self.time_order+1)
        first_time_index = 0
        step_backwards = -1
        if self._forward is not True:
            time_stepper_indices = reversed(time_stepper_indices)
            first_time_index = self.time_order
            step_backwards = 1
        for i in time_stepper_indices:
            lhs = self.time_steppers[i].name
            if i == first_time_index:
                rhs = ccode(ti % (self.time_order+1))
            else:
                rhs = ccode((self.time_steppers[i+step_backwards]+1) % (self.time_order+1))
            body.append(cgen.Assign(lhs, rhs))

        return body

    def time_substitutions(self, sympy_expr):
        """This method checks through the sympy_expr to replace the time index with a cyclic index
        but only for variables which are not being saved in the time domain
        """
        if isinstance(sympy_expr, Indexed):
            array_term = sympy_expr
            if not self.save_vars[str(array_term.base.label)]:
                array_term = array_term.xreplace(self.t_replace)
            return array_term
        else:
            for arg in sympy_expr.args:
                sympy_expr = sympy_expr.subs(arg, self.time_substitutions(arg))
        return sympy_expr