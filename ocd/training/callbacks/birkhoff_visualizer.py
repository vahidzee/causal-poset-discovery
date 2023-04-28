"""
This file contains a callback that visualizes the explorability of the model.

The explorability of the model is a concept based on the distribution of permutations that 
are generated by the model at each phase.

We expect the model starting off exploring a lot of permutations and then ends up concentrating
on a key set of permutations that resemble a correct ordering. To do that, we use a visualization
of the permutation matrices using the doubly stochastic Birkhoff polytope.

In the beginning we will train a PCA on the Birkhoff polytope of some size, and then, after 
each phase change we will get all the logged permutations of that phase, feed it to the pre-trained
PCA and do a scatter plot to show the distribution of the permutations in that phase.
"""

import lightning.pytorch as pl
from .logging import LoggingCallback
import numpy as np
from sklearn.decomposition import PCA
import matplotlib.pyplot as plt
from ocd.models.permutation.utils import sinkhorn
import torch
from itertools import permutations
import typing as th
from lightning_toolbox import TrainingModule
from ocd.visualization.birkhoff import visualize_exploration
import networkx as nx
from ocd.evaluation import backward_relative_penalty
from scipy.optimize import linear_sum_assignment

MARKERS = ["^", "o", "x"]


def get_core_points(
    permutation_size: int,
    num_points: int,
    birkhoff_edges: bool = False,
    birkhoff_vertices: bool = True,
):
    """
    Given a permutation_size, this function returns the core points of the Birkhoff polytope.

    The core points can be the following:
    1. The permutation matrices of size permutation_size, this will correspond to the vertices
        of the Birkhoff polytope.
    2. The mid-way points between the permutation matrices, this will correspond to the edges
        of the Birkhoff polytope.

    After listing all the core points according to the priority provided above, we will then sample
    num_points from the list of core points. This sampling is done iteratively so that each core point
    will have the most distance from the previous set of points.

    Args:
        permutation_size: the size of the permutation matrices
        birkhoff_edges: whether to include the mid-way points between the permutation matrices
        birkhoff_vertices: whether to include the permutation matrices

    Returns:
        core_points: a tensor of shape (num_core_points, permutation_size, permutation_size)
    """

    # create all the permutation matrices of size permutation_size
    # and append them to the core_points
    core_points = None

    # add birkhoff_vertices to the core_points
    if birkhoff_vertices:
        for perm in permutations(range(permutation_size)):
            # create a permutation matrix out of perm
            perm_mat = np.zeros((permutation_size, permutation_size))
            perm_mat[perm, np.arange(permutation_size)] = 1
            # extend perm_mat in the first dimension
            perm_mat = np.expand_dims(perm_mat, axis=0)
            # concatenate perm_mat to the birkoff_vertices
            core_points = perm_mat if core_points is None else np.concatenate([core_points, perm_mat], axis=0)

    # add birkhoff_edges to the core_points
    if birkhoff_edges:
        edge_points = []
        for perm1 in permutations(range(permutation_size)):
            for perm2 in permutations(range(permutation_size)):
                # create a permutation matrix out of perm
                perm_mat1 = np.zeros((permutation_size, permutation_size))
                perm_mat1[perm1, np.arange(permutation_size)] = 1
                # create a permutation matrix out of perm
                perm_mat2 = np.zeros((permutation_size, permutation_size))
                perm_mat2[perm2, np.arange(permutation_size)] = 1
                # create the edge point
                edge_point = (perm_mat1 + perm_mat2) / 2
                # append the edge point to the edge_points
                edge_points.append(edge_point)
        # concatenate all the edge points to the core_points
        t = np.stack(edge_points, axis=0)
        core_points = t if core_points is None else np.concatenate([core_points, t], axis=0)

    # Now iteratively sample num_points from the core_points
    sampled_core_points = None
    num_points = min(num_points, core_points.shape[0])
    for i in range(num_points):
        # if this is the first point, just sample the first point
        if i == 0:
            sampled_core_points = core_points[0]
            # expand the sampled_core_points in the first dimension
            sampled_core_points = np.expand_dims(sampled_core_points, axis=0)
        else:
            set_a = sampled_core_points.reshape(sampled_core_points.shape[0], -1)
            set_b = core_points.reshape(core_points.shape[0], -1)
            # get an i x core_points.shape[0] matrix of distances
            # between the sampled_core_points and the core_points
            dist = np.linalg.norm(
                np.expand_dims(set_a, axis=1) - np.expand_dims(set_b, axis=0),
                axis=2,
            )
            # for each core_point corresponding to the columns in dist,
            # get the minimum distance from the sampled_core_points
            dist = np.min(dist, axis=0)

            # get the column with the maximum distance
            idx = np.argmax(dist)
            # add the point with the maximum distance to the sampled_core_points
            sampled_core_points = np.concatenate(
                [sampled_core_points, np.expand_dims(core_points[idx], axis=0)], axis=0
            )

    # return the sampled_core_points
    return sampled_core_points


def cluster_particles(all_points: np.array, core_points: np.array) -> np.array:
    """
    This function clusters all the particles according to how close they are to
    core_points

    Args:
        all_points: [n_samples, permutation_size, permutation_size]
        core_points: [n_core_points, permutation_size, permutation_size]
    Returns:
        clusters: a one dimensional np.array of size n_samples that assigns each point to
                    a cluster
    """
    # cluster doubly_stochastic_matrices according to what index of
    # core_points they are closest to
    clusters = np.zeros(all_points.shape[0])
    for i, mat in enumerate(all_points):
        # get the index of the closest vertex
        row_ind, col_ind = linear_sum_assignment(-mat)
        mat_after_hungarian = np.zeros_like(mat)
        mat_after_hungarian[row_ind, col_ind] = 1
        closest_vertex = np.argmin(np.linalg.norm(core_points - mat_after_hungarian, axis=(1, 2)))
        clusters[i] = closest_vertex
    return clusters


def get_birkhoff_samples(permutation_size: int, n_sample: int = 100) -> np.array:
    """
    Args:
        permutation_size: the size of the permutation matrices
        n_sample: the number of samples to draw from the polytope
    Returns:
        polytope: a tensor of shape (n_sample, permutation_size, permutation_size) as a numpy array
    """
    # sample n_sample x permutation_size x permutation_size gumbel noises
    gumbel_noise = np.random.gumbel(size=(n_sample, permutation_size, permutation_size))
    # turn the gumbel noise into a torch tensor
    gumbel_noise = torch.from_numpy(gumbel_noise).float()
    polytope = (
        sinkhorn(torch.cat([gumbel_noise, gumbel_noise / 0.1, gumbel_noise / 0.05], dim=0), 100).detach().cpu().numpy()
    )
    return polytope


class BirkhoffCallback(LoggingCallback):
    def __init__(
        self,
        evaluate_every_n_epochs: int = 1,
        # TODO: Change this into a method with dynamize
        evaluate_every_n_epoch_logic: th.Optional[str] = None,
        epoch_buffer_size: int = 1,
        log_training: bool = True,
        log_validation: bool = False,
        permutation_size: th.Optional[int] = None,
        seed: th.Optional[int] = None,
        # PCA setting
        fit_every_time: bool = False,
        # loss values printed
        write_cost_values: bool = False,
        write_permutation_names: bool = False,
        loss_cluster_count: int = 100,
        core_points_has_birkhoff_vertices: bool = True,
        core_points_has_birkhoff_edges: bool = False,
        # Including correct orderings
        ordering_to_score_mapping: th.Optional[th.Dict[str, int]] = None,
        # Include permutation names
        add_permutation_to_name: bool = False,
        # Reject outlier cost values
        reject_outlier_factor: th.Optional[float] = None,
        # The causal graph
        causal_graph: th.Optional[nx.DiGraph] = None,
    ) -> None:
        """
        This is a lightning callback that visualizes how the model explores and behaves.
        In summary, it visualizes all the latent permutations that are sampled at each of the maximization
        and expectation steps. To do so, we use PCA on the Birkhoff polytope to project the permutations
        to 2D. We then visualize the 2D projections of the permutations.

        Using the following argument you can control the logging process:
            log_on_phase_change: whether to log the explorability when the phase of the training module changes
            log_every_n_epochs: whether to log the explorability every n epochs

        Args:
            permutation_size: the size of the permutation
            seed: the seed to use for the numpy random number generator
            log_on_phase_change: whether to log the explorability when the phase changes
            log_every_n_epochs: whether to log the explorability every n epochs
            fit_every_time: whether to fit the PCA every time or not
            write_loss_on_vertices: whether to write the loss on vertices or not
            loss_on_vertices_count: the number of vertices to write the loss on
            clear_logs_each_epoch: whether to clear the logs each epoch or not
            scm: the SCM object to use for finding the points associated with each ordering
            add_permutation_to_name: If this is set to true, then the average of each cluster
                                     is written in the legend as an approximate permutation
        """
        super().__init__(
            evaluate_every_n_epochs=evaluate_every_n_epochs,
            evaluate_every_n_epoch_logic=evaluate_every_n_epoch_logic,
            epoch_buffer_size=epoch_buffer_size,
            log_training=log_training,
            log_validation=log_validation,
        )
        # Infer permutation size from scm if not given
        if permutation_size is None:
            return
        self.permutation_size = permutation_size

        self.fit_every_time = fit_every_time
        self.pca = PCA(n_components=2)

        self.add_permutation_to_name = add_permutation_to_name

        self.seed = seed
        self.reject_outlier_factor = reject_outlier_factor
        # set the seed of numpy
        np.random.seed(self.seed)

        # If we do not want to fit each and every time, then we need to sample
        # a Birkhoff polytope as a palette to start with and fit a core
        # PCA on it to visualize everything in two dimensions

        self.polytope = None
        self.transformed_polytope = None

        if not self.fit_every_time:
            self.polytope = get_birkhoff_samples(permutation_size)
            # train a PCA on all the elements of the polytope
            self.pca.fit(self.polytope.reshape(-1, permutation_size * permutation_size))
            self.transformed_polytope = self.pca.transform(
                self.polytope.reshape(-1, permutation_size * permutation_size)
            )

        # If we have to log the losses as well, we should get a set of core points
        # and save them
        self.write_cost_values = write_cost_values
        if self.write_cost_values:
            self.cluster_count = loss_cluster_count
            self.core_points = get_core_points(
                permutation_size,
                self.cluster_count,
                birkhoff_vertices=core_points_has_birkhoff_vertices,
                birkhoff_edges=core_points_has_birkhoff_edges,
            )

        # For each of the vertex points which are the permutation
        # set their delimiters according to the number of backward edges
        # they have. If something has a low number of backward edges, then
        # it will have a larger delimiter. This is used to determine the correct
        # orderings
        self.birkhoff_vertices = get_core_points(
            permutation_size, permutation_size**permutation_size, birkhoff_edges=False, birkhoff_vertices=True
        )

        self.birkhoff_vertex_scores = []
        self.birkhoff_vertex_names = []
        for perm in self.birkhoff_vertices:
            ordering = perm.argmax(-2).tolist()
            ordering_str = "-".join([str(x) for x in ordering])
            self.birkhoff_vertex_names.append(ordering_str)

            if ordering_to_score_mapping is None:
                if causal_graph is None:
                    # ignore in this case and just add -1
                    self.birkhoff_vertex_scores.append(-1)
                else:
                    self.birkhoff_vertex_scores.append(backward_relative_penalty(ordering, causal_graph))
            else:
                if ordering_str not in ordering_to_score_mapping:
                    raise ValueError(
                        "The ordering {} was not found in the ordering_to_score_mapping".format(ordering_str)
                    )
                else:
                    self.birkhoff_vertex_scores.append(ordering_to_score_mapping[ordering_str])
        if not write_permutation_names:
            self.birkhoff_vertex_names = None
        self.birkhoff_vertex_scores = np.array(self.birkhoff_vertex_scores)

    def on_fit_start(self, trainer: pl.Trainer, pl_module: TrainingModule) -> None:
        if not hasattr(pl_module, "current_phase"):
            raise ValueError(
                "The Birkhoff callback only sits on top of a training module that has a current_phase attribute\nConsider adding a PhaseChangerCallback to your callbacks"
            )

    def _print_unique_permutations(self, logged_permutations):
        real_logged_permutations = logged_permutations.argmax(axis=-2)
        # get the unique rows and the number of times they appear
        unique_rows, counts = np.unique(real_logged_permutations, axis=0, return_counts=True)
        print("Permutations that were seen:")
        for row, count in zip(unique_rows, counts):
            print(row, " : ", count, " times")

    def evaluate(self, trainer: pl.Trainer, pl_module: TrainingModule) -> None:
        # Get the logged permutations
        logged_permutations = torch.cat(self.all_logged_values["permutation_to_display"], dim=0).detach().cpu().numpy()
        logged_losses = -torch.cat(self.all_logged_values["log_prob_to_display"], dim=0).detach().cpu().numpy()

        # Use the hard permutations if available for clustering
        permutations_used_for_clustering = (
            torch.cat(self.all_logged_values["elementwise_perm_mat"], dim=0).detach().cpu().numpy()
        )
        if len(logged_permutations) != len(permutations_used_for_clustering):
            permutations_used_for_clustering = logged_permutations

        # If we are to train the PCA every time, then we should fit it with the logged permutations here
        if self.fit_every_time:
            self.pca.fit(logged_permutations.reshape(-1, self.permutation_size * self.permutation_size))

        # Now get a permutation without noise from the model for representing the current state
        # of the permutation learner
        permutation_without_noise = pl_module.model.permutation_model.get_permutation_without_noise()

        clusters = None
        cost_values = logged_losses
        # If the logger wants to write the cost values, then we should cluster the points
        # and write the cost values at the centroid of each cluster
        if self.write_cost_values:
            clusters = cluster_particles(permutations_used_for_clustering, self.core_points)

        # Generate the image
        img = visualize_exploration(
            visualization_model=self.pca,
            backbone=self.transformed_polytope,
            backbone_is_transformed=True,
            sampled_permutations=logged_permutations,
            clusters=clusters,
            cost_values=cost_values,
            outliers_factor=self.reject_outlier_factor,
            permutation_without_noise=permutation_without_noise,
            birkhoff_vertices=self.birkhoff_vertices,
            birkhoff_vertices_cost=self.birkhoff_vertex_scores,
            birkhoff_vertices_name=self.birkhoff_vertex_names,
            add_permutation_to_name=self.add_permutation_to_name,
            colorbar_label="count backwards",
            image_size=(15, 10),
            title="Birkhoff Polytope of Permutations",
            ylabel=f"phase {pl_module.current_phase}",
            xlabel=f"epoch: {pl_module.current_epoch}",
        )

        # Log the image
        trainer.logger.log_image(key="explorability/birkhoff", images=[img], caption=["Training bird-eye view"])
