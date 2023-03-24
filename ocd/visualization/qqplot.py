import torch
import typing as th
import matplotlib.pyplot as plt
import numpy as np


def qqplot(a: torch.Tensor, b: torch.Tensor, a_name: str, b_name: str, image_size: th.Tuple):
    res = []

    for i in range(a.shape[1]):
        x_samples = a[:, i].detach().cpu().numpy().flatten()
        y_samples = b[:, i].detach().cpu().numpy().flatten()

        fig, ax = plt.subplots()
        # customize the image size if needed
        if image_size:
            fig.set_size_inches(image_size[0], image_size[1])
        try:
            ax.set_title(f"{i+1}'th column")
            ax.set_xlabel(a_name)
            ax.set_ylabel(b_name)
            mn = min(np.min(x_samples), np.min(y_samples))
            mx = max(np.max(x_samples), np.max(y_samples))
            ax.plot(np.linspace(mn, mx, 100), np.linspace(mn, mx, 100), c="red", alpha=0.2, label="y=x")
            x_samples = np.sort(x_samples)
            y_samples = np.sort(y_samples)
            ax.scatter(x_samples, y_samples, s=1, alpha=0.2, label="samples")

            ax.legend()

            # draw everything to the figure for conversion
            fig.canvas.draw()
            # convert the figure to a numpy array
            data = np.fromstring(fig.canvas.tostring_rgb(), dtype=np.uint8, sep="")
            data = data.reshape(fig.canvas.get_width_height()[::-1] + (3,))
        finally:
            plt.close()

        res.append(data)

    return res