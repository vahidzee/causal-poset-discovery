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
from lightning.pytorch.callbacks import Callback
import numpy as np
from sklearn.decomposition import PCA
import matplotlib.pyplot as plt


class ExplorabilityCallback(Callback):
    def __init__(
        self,
        permutation_size: int,
        seed=None,
        n_sample=200,
        log_on_phase_change: bool = True,
        log_every_n_epochs: int = 0,
        fit_every_time: bool = False,
    ) -> None:
        self.permutation_size = permutation_size
        self.last_saved_phase = None

        self.log_on_phase_change = log_on_phase_change
        self.log_every_n_epochs = log_every_n_epochs
        self.log_every_n_epochs_counter = 0
        self.fit_every_time = fit_every_time
        self.pca = PCA(n_components=2)

        self.seed = seed

        if not self.fit_every_time:
            # create a random tensor of size n_sample x permutation_size x permutation_size
            polytope = np.random.randn(n_sample // 2, permutation_size, permutation_size)
            # add n_sample number of permutation matrices to the polytope
            for i in range(n_sample - n_sample // 2):
                new_perm = np.eye(permutation_size)[np.random.permutation(permutation_size)]
                # unsqueeze new_param to make it 3D
                new_perm = np.expand_dims(new_perm, axis=0)
                # concatenate the new permutation matrix to the polytope
                polytope = np.concatenate((polytope, new_perm), axis=0)

            # Do alternative row and column normalization
            for _ in range(100):
                polytope = polytope / np.sum(polytope, axis=-1, keepdims=True)
                polytope = polytope / np.sum(polytope, axis=-2, keepdims=True)

            self.polytope = polytope

            # train a PCA on all the elements of the polytope
            self.pca.fit(polytope.reshape(-1, permutation_size * permutation_size))

    def print_unique_permutations(self, logged_permutations):
        real_logged_permutations = logged_permutations.argmax(axis=-1)
        # get the unique rows and the number of times they appear
        unique_rows, counts = np.unique(real_logged_permutations, axis=0, return_counts=True)
        print("Permutations that were seen:")
        for row, count in zip(unique_rows, counts):
            print(row, " : ", count, " times")

    def check_should_log(self, pl_module: pl.LightningModule) -> bool:
        # If the logging is not at the end of the phase change then
        # check frequency and return accordingly
        if not self.log_on_phase_change:
            t = self.log_every_n_epochs_counter
            self.log_every_n_epochs_counter = (t + 1) % self.log_every_n_epochs
            return t == self.log_every_n_epochs - 1

        # If the last phase is the same as the current phase, do nothing
        # This indicates no phase change edge
        if pl_module.get_phase() == self.last_saved_phase:
            return False
        # Do nothing if the last phase was None
        # (This means this is the first time that it is being called)
        if self.last_saved_phase is None:
            self.last_saved_phase = pl_module.get_phase()
            return False
        # A phase change has happened and now is the time to visualize the explorability
        self.last_saved_phase = pl_module.get_phase()
        return True

    def on_train_epoch_end(self, trainer: pl.Trainer, pl_module: pl.LightningModule) -> None:
        ret = super().on_train_epoch_end(trainer, pl_module)

        # Get all the logged permutations of that epoch and clear the log
        # (this is a percaution to make sure memory is not filled up)

        logged_permutations = pl_module.model.get_logged_permutations().numpy()

        pl_module.model.clear_logged_permutations()

        if not self.check_should_log(pl_module):
            return ret

        # transform the logged permutations using the PCA
        if self.fit_every_time:
            self.pca.fit(logged_permutations.reshape(-1, self.permutation_size * self.permutation_size))

        transformed_permutations = self.pca.transform(
            logged_permutations.reshape(-1, self.permutation_size * self.permutation_size)
        )

        # get the root tensorboard logger
        logger = pl_module.logger.experiment

        # plot the permutation using matplotlib and save it to a numpy array
        fig, ax = plt.subplots()

        try:
            ax.set_title("Birkhoff Polytope of Permutations")
            ax.set_ylabel(f"phase {pl_module.get_phase()}")
            ax.set_xlabel(f"epoch: {pl_module.current_epoch}")
            ax.scatter(transformed_permutations[:, 0], transformed_permutations[:, 1], s=1)
            fig.canvas.draw()
            # convert the figure to a numpy array
            data = np.fromstring(fig.canvas.tostring_rgb(), dtype=np.uint8, sep="")
            data = data.reshape(fig.canvas.get_width_height()[::-1] + (3,))
            # log the figure to tensorboard
            logger.add_image(f"explorability/all_perm_mat", data, pl_module.current_epoch, dataformats="HWC")
        finally:
            plt.close()

        return ret
