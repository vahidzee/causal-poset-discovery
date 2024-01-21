import torch
import abc

from ocd.models import OSlow
from ocd.training.utils import sample_gumbel_noise, hungarian, turn_into_matrix


class PermutationLearningModule(torch.nn.Module, abc.ABC):
    def __init__(self, in_features: int):
        super().__init__()
        self.register_parameter(
            "gamma", torch.nn.Parameter(torch.randn(in_features, in_features))
        )

    def sample_hard_permutations(self, num_samples: int):
        gumbel_noise = sample_gumbel_noise(
            (num_samples, *self.gamma.shape), device=self.gamma.device
        )
        permutations = hungarian(self.gamma + gumbel_noise).to(self.gamma.device)
        # turn permutations into permutation matrices
        ret = torch.stack([turn_into_matrix(perm) for perm in permutations])
        # add some random noise to the permutation matrices # TODO why?
        return ret

    def loss(self, batch: torch.Tensor, model: OSlow) -> torch.Tensor:
        raise NotImplementedError


class GumbelTopK(PermutationLearningModule):
    def __init__(self, in_features: int, num_samples: int):
        super().__init__(in_features)
        self.num_samples = num_samples

    def loss(self, model: OSlow, batch: torch.Tensor) -> torch.Tensor:
        permutations = self.sample_hard_permutations(self.num_samples)
        unique_perms = torch.unique(permutations, dim=0)
        b_size = batch.shape[0]
        n_unique = unique_perms.shape[0]

        # shape: (num_uniques, )
        scores = torch.sum(
            unique_perms.reshape(unique_perms.shape[0], -1) * self.gamma.reshape(1, -1),
            dim=-1,
        )
        unique_perms = unique_perms.repeat_interleave(b_size, dim=0)
        batch = batch.repeat(n_unique, 1, 1)  # shape: (batch * num_uniques, d, d)

        log_probs = model.log_prob(
            batch, perm_mat=unique_perms
        )  # shape: (batch * num_uniques, )

        log_probs = log_probs.reshape(n_unique, b_size)
        losses = -log_probs.mean(axis=-1)  # shape: (num_uniques, )

        return torch.exp(scores) @ losses / torch.exp(scores).sum()
