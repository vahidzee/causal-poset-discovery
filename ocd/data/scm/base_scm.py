import networkx as nx
import numpy as np
import typing as th
import pandas as pd


class SCM:
    """
    An SCM object generated by the SCMGenerator class. This class is used to simulate data from a given SCM.
    It also contains a function to draw the SCM structure and the formulas relating each node to its parents.
    """

    def __init__(
        self,
        # The graph
        dag: nx.DiGraph,
        # The SCM parameters
        noise_parameters: th.Dict[th.Any, th.Any],
        node_parameters: th.Dict[th.Any, th.Any],
        parent_parameters: th.Dict[th.Any, th.List],
        parents: th.Dict[th.Any, th.List],
        # The actual data generating functions
        get_exogenous_noise: th.Optional[th.Callable] = None,
        get_covariate_from_parents: th.Optional[th.Callable] = None,
        # The function that generates the formula for each node (for mere visualization purposes)
        get_covariate_from_parents_signature: th.Optional[th.Callable] = None,
    ):
        """
        Args:
            dag: a networkx.DiGraph
            noise_parameters: a dictionary of parameters for each noise in node
            node_parameters: a dictionary of parameters for each node
            parent_parameters: A dictionary relating each node to a list of parameters for each parent
            parents: A dictionary relating each node to a list of parents

            get_exogenous_noise: a function that takes a seed and returns a noise
            get_covariate_from_parents: a function that takes a list of inputs and a list of parameters and returns a covariate
            get_covariate_from_parents_signature: a function that takes a list of inputs and a list of parameters and returns a
                                        string representation of the covariate
            For more information on how to implement these functions, check out the function documentation in
            ocd/data/scm_generators.py
        """
        self.dag = dag
        self.noise_parameters = noise_parameters
        self.node_parameters = node_parameters
        self.parent_parameters = parent_parameters
        self.parents = parents

        self.topological_order = list(nx.topological_sort(dag))

        self.get_exogenous_noise = get_exogenous_noise
        self.get_covariate_from_parents = get_covariate_from_parents
        self.get_covariate_from_parents_signature = get_covariate_from_parents_signature

        self.fill_out_labels()

    def simulate(
        self,
        n_samples: int,
        seed: th.Optional[int] = None,
        intervention_nodes: th.Optional[th.List[th.Any]] = None,
        intervention_functions: th.Optional[th.List[th.Callable]] = None,
    ) -> pd.DataFrame:
        """
        This function returns a dataframe containing a number of simulations ran on the data.
        The simulations will by default generate observational data. However, the capability of
        implementing interventions is also included. If intervention_node is not None, then a number of
        interventions will be applied to the nodes specified in intervention_nodes. The intervention
        on node 'i' will be defined by the function intervention_functions[i].

        The intervention_functions[i] function must take in the following arguments:
        ===
        intervention_function[i] signature:
            Args:
                noise (float): The value of the noise in that node,
                parents (th.List[float]): A list of the values assigned to all the parents of a node,
                parent_parameters (th.List[th.Dict[str, th.Any]]):
                    A list of dictionaries containing the parameters for each parent of a node
                node_parameters (th.Dict[str, th.Any]): A dictionary containing the parameters for a node

            Returns:
                The value of the node after doing an intervention
        ===

        Args:
            n_samples (int): The number of samples we are trying to draw
            seed (int, optional): _description_. Defaults to None.
            intervention_node (th.Optional[th.Any], optional): _description_. Defaults to None.
            intervention_function (th.Optional[th.Callable], optional): _description_. Defaults to None.

        Returns:
            pandas.DataFrame: A dataframe containing the simulated data
        """
        # Create a dictionary from the intervention nodes to their corresponding intervention functions
        if intervention_nodes is None:
            intervention_nodes = []
            intervention_functions = []
        intervention_node_to_function = dict(list(zip(intervention_nodes, intervention_functions)))

        # All the values per columns
        vals = {x: None for x in self.dag.nodes}

        # seed equals a random number if seed is None
        seed = np.random.randint(0, 100000) if seed is None else seed

        for ct, v in enumerate(self.topological_order):
            noises = self.get_exogenous_noise(
                self.noise_parameters[v],
                seed=seed + ct,
                n_samples=n_samples,
            )

            # Since this function is custom, raise exception if it does not
            # generate n_samples number of values.
            if len(noises) != n_samples:
                exception_str = f"The number of samples generated by the noise function is not equal to n_samples.\n"
                exception_str += (
                    "Try rewriting your get_exogenous_noise function to incorporate the n_samples argument."
                )
                raise Exception(exception_str)

            func = (
                intervention_node_to_function[v]
                if v in intervention_node_to_function
                else self.get_covariate_from_parents
            )
            parent_values = []
            for u in self.parents[v]:
                if vals[u] is None:
                    raise Exception(f"Parent {u} of node {v} has not been assigned a value yet")
                parent_values.append(vals[u])
            vals[v] = func(noises, parent_values, self.parent_parameters[v], self.node_parameters[v])

        samples = pd.DataFrame(vals)
        return samples

    @property
    def n(self):
        return self.dag.number_of_nodes()

    def nodes(self):
        return self.dag.nodes

    def fill_out_labels(self):
        self.node_labels = {}
        for v in self.topological_order:
            if self.get_covariate_from_parents_signature is not None:
                self.node_labels[v] = self.get_covariate_from_parents_signature(
                    v, self.parents[v], self.node_parameters[v], self.noise_parameters[v], self.parent_parameters[v]
                )
            else:
                self.node_labels[v] = f"x{(v)}"

    def get_description(self) -> str:
        ret = ""
        # print all the values in node_labels
        for _, v in self.node_labels.items():
            ret += f"{v}\n---------------------\n"
        return ret

    def draw(self, with_labels: bool = False):
        """
        Prints out the SCM structure using networkx and also writes down the formulas relating each node to its parents.

        Args:
            with_labels (bool, optional): _description_. Defaults to False.
        """
        # take a copy of self.dag
        dag = self.dag.copy()

        # get node positions from networkx
        pos = nx.drawing.layout.spring_layout(dag)

        nx.draw(dag, pos=pos, with_labels=not with_labels)
        if with_labels:
            nx.draw_networkx_labels(dag, pos=pos, labels=self.node_labels, font_size=8)
        else:
            print(self.get_description())

    def count_backward(self, ordering: th.List[int]) -> int:
        """
        If we apply ordering to the graph of self.dag, how many edges will be reversed?
        """
        # get the index of each node in ordering
        ordering = {v: i for i, v in enumerate(ordering)}
        # count the number of edges that will be reversed
        count = 0
        for u, v in self.dag.edges:
            if ordering[u] > ordering[v]:
                count += 1
        return count

    @property
    def ordering(self):
        # return the topological ordering of self.dag
        return list(nx.topological_sort(self.dag))
