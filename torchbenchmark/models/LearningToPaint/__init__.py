import cv2
import torch
import random
import numpy as np
from .baseline.Renderer.model import FCN
from .baseline.DRL.evaluator import Evaluator
from .baseline.utils.util import *
from .baseline.DRL.ddpg import DDPG
from .baseline.DRL.multi import fastenv

from ...util.model import BenchmarkModel
from torchbenchmark.tasks import REINFORCEMENT_LEARNING

from argparse import Namespace

torch.manual_seed(1337)
np.random.seed(1337)
torch.backends.cudnn.deterministic = False
torch.backends.cudnn.benchmark = True


class Model(BenchmarkModel):
    task = REINFORCEMENT_LEARNING.OTHER_RL

    def __init__(self, device=None, jit=False):
        super(Model, self).__init__()
        self.device = device
        self.jit = jit
        # All of these options are from source code. The paper mentions batch size is 96.
        self.args = Namespace(**{
            'validate_episodes': 5,
            'validate_interval': 50,
            'max_step': 40,
            'env_batch': 64,
            'batch_size': 96,
            'discount': 0.95**5,
            'episode_train_times': 10,
            'noise_factor': 0.0,
        })
        # The paper mentions input images are from CelebFaces and resized to 128 x 128.
        # Create 2000 random tensors for input, but fastenv will still load 200,000 images.
        self.width = 128
        self.image_examples = torch.rand(2000, 3, self.width, self.width)

        # LearningToPaint includes actor, critic, and discriminator models, make sure to run all of them!
        self.Decoder = FCN()
        self.env = fastenv(max_episode_length=self.args.max_step, env_batch=self.args.env_batch,
                           images=self.image_examples, device=self.device, Decoder=self.Decoder)
        self.agent = DDPG(batch_size=self.args.batch_size, env_batch=self.args.env_batch, max_step=self.args.max_step,
                          discount=self.args.discount, device=self.device, Decoder=self.Decoder)
        self.evaluate = Evaluator(args=self.args, writer=None)
        self.step = 0
        self.observation = self.env.reset()
        self.agent.reset(self.observation, self.args.noise_factor)

    def get_module(self):
        action = self.agent.select_action(self.observation, noise_factor=self.args.noise_factor)
        self.observation, reward, done, _ = self.env.step(action)
        self.agent.observe(reward, self.observation, done, self.step)
        state, action, reward, \
            next_state, terminal = self.agent.memory.sample_batch(self.args.batch_size, self.device)
        state = torch.cat((state[:, :6].float() / 255, state[:, 6:7].float() / self.args.max_step,
                           self.agent.coord.expand(state.shape[0], 2, 128, 128)), 1)
        return self.agent.actor, (state, )

    def train(self, niter=1):
        if self.jit:
            raise NotImplementedError()
        episode = episode_steps = 0
        for _ in range(niter):
            episode_steps += 1
            if self.observation is None:
                self.observation = self.env.reset()
                self.agent.reset(self.observation, self.args.noise_factor)
            action = self.agent.select_action(self.observation, noise_factor=self.args.noise_factor)
            self.observation, reward, done, _ = self.env.step(action)
            self.agent.observe(reward, self.observation, done, self.step)
            if (episode_steps >= self.args.max_step and self.args.max_step):
                # [optional] evaluate
                if episode > 0 and self.args.validate_interval > 0 and \
                        episode % self.args.validate_interval == 0:
                    reward, dist = self.evaluate(self.env, self.agent.select_action)
                tot_Q = 0.
                tot_value_loss = 0.
                lr = (3e-4, 1e-3)
                for i in range(self.args.episode_train_times):
                    Q, value_loss = self.agent.update_policy(lr)
                    tot_Q += Q.data.cpu().numpy()
                    tot_value_loss += value_loss.data.cpu().numpy()
                # reset
                self.observation = None
                episode_steps = 0
                episode += 1
            self.step += 1

    def eval(self, niter=1):
        if self.jit:
            raise NotImplementedError()
        for _ in range(niter):
            reward, dist = self.evaluate(self.env, self.agent.select_action)

    def _set_mode(self, train):
        if train:
            self.agent.train()
        else:
            self.agent.eval()

if __name__ == '__main__':
    m = Model(device='cpu', jit=False)
    module, example_inputs = m.get_module()
    while m.step < 100:
        m.train(niter=1)
        if m.step % 100 == 0:
            m.eval(niter=1)
        m.step += 1
