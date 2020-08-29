from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union

import numpy as np
import torch as th
from torch.nn import functional as F
from tqdm import tqdm

from stable_baselines3.common import logger
from stable_baselines3.common.buffers import ReplayBuffer
from stable_baselines3.common.noise import ActionNoise
from stable_baselines3.common.off_policy_algorithm import OffPolicyAlgorithm
from stable_baselines3.common.preprocessing import get_action_dim
from stable_baselines3.common.type_aliases import GymEnv, MaybeCallback, ReplayBufferSamples
from stable_baselines3.common.utils import polyak_update
from stable_baselines3.sac.policies import SACPolicy


class SAC(OffPolicyAlgorithm):
    """
    Soft Actor-Critic (SAC)
    Off-Policy Maximum Entropy Deep Reinforcement Learning with a Stochastic Actor,
    This implementation borrows code from original implementation (https://github.com/haarnoja/sac)
    from OpenAI Spinning Up (https://github.com/openai/spinningup), from the softlearning repo
    (https://github.com/rail-berkeley/softlearning/)
    and from Stable Baselines (https://github.com/hill-a/stable-baselines)
    Paper: https://arxiv.org/abs/1801.01290
    Introduction to SAC: https://spinningup.openai.com/en/latest/algorithms/sac.html

    Note: we use double q target and not value target as discussed
    in https://github.com/hill-a/stable-baselines/issues/270

    :param policy: (SACPolicy or str) The policy model to use (MlpPolicy, CnnPolicy, ...)
    :param env: (GymEnv or str) The environment to learn from (if registered in Gym, can be str)
    :param learning_rate: (float or callable) learning rate for adam optimizer,
        the same learning rate will be used for all networks (Q-Values, Actor and Value function)
        it can be a function of the current progress remaining (from 1 to 0)
    :param buffer_size: (int) size of the replay buffer
    :param learning_starts: (int) how many steps of the model to collect transitions for before learning starts
    :param batch_size: (int) Minibatch size for each gradient update
    :param tau: (float) the soft update coefficient ("Polyak update", between 0 and 1)
    :param gamma: (float) the discount factor
    :param train_freq: (int) Update the model every ``train_freq`` steps. Set to `-1` to disable.
    :param gradient_steps: (int) How many gradient steps to do after each rollout
        (see ``train_freq`` and ``n_episodes_rollout``)
        Set to ``-1`` means to do as many gradient steps as steps done in the environment
        during the rollout.
    :param n_episodes_rollout: (int) Update the model every ``n_episodes_rollout`` episodes.
        Note that this cannot be used at the same time as ``train_freq``. Set to `-1` to disable.
    :param action_noise: (ActionNoise) the action noise type (None by default), this can help
        for hard exploration problem. Cf common.noise for the different action noise type.
    :param optimize_memory_usage: (bool) Enable a memory efficient variant of the replay buffer
        at a cost of more complexity.
        See https://github.com/DLR-RM/stable-baselines3/issues/37#issuecomment-637501195
    :param ent_coef: (str or float) Entropy regularization coefficient. (Equivalent to
        inverse of reward scale in the original SAC paper.)  Controlling exploration/exploitation trade-off.
        Set it to 'auto' to learn it automatically (and 'auto_0.1' for using 0.1 as initial value)
    :param target_update_interval: (int) update the target network every ``target_network_update_freq``
        gradient steps.
    :param target_entropy: (str or float) target entropy when learning ``ent_coef`` (``ent_coef = 'auto'``)
    :param use_sde: (bool) Whether to use generalized State Dependent Exploration (gSDE)
        instead of action noise exploration (default: False)
    :param sde_sample_freq: (int) Sample a new noise matrix every n steps when using gSDE
        Default: -1 (only sample at the beginning of the rollout)
    :param use_sde_at_warmup: (bool) Whether to use gSDE instead of uniform sampling
        during the warm up phase (before learning starts)
    :param create_eval_env: (bool) Whether to create a second environment that will be
        used for evaluating the agent periodically. (Only available when passing string for the environment)
    :param policy_kwargs: (dict) additional arguments to be passed to the policy on creation
    :param verbose: (int) the verbosity level: 0 no output, 1 info, 2 debug
    :param seed: (int) Seed for the pseudo random generators
    :param device: (str or th.device) Device (cpu, cuda, ...) on which the code should be run.
        Setting it to auto, the code will be run on the GPU if possible.
    :param _init_setup_model: (bool) Whether or not to build the network at the creation of the instance
    """

    def __init__(
        self,
        policy: Union[str, Type[SACPolicy]],
        env: Union[GymEnv, str],
        learning_rate: Union[float, Callable] = 3e-4,
        buffer_size: int = int(1e6),
        learning_starts: int = 100,
        batch_size: int = 256,
        tau: float = 0.005,
        gamma: float = 0.99,
        train_freq: int = 1,
        gradient_steps: int = 1,
        n_episodes_rollout: int = -1,
        action_noise: Optional[ActionNoise] = None,
        optimize_memory_usage: bool = False,
        replay_buffer_class: Optional[Type[ReplayBuffer]] = None,
        replay_buffer_kwargs: Optional[Dict[str, Any]] = None,
        ent_coef: Union[str, float] = "auto",
        target_update_interval: int = 1,
        target_entropy: Union[str, float] = "auto",
        initial_alpha: float = 5.0,
        alpha_threshold: float = 10.0,
        n_action_samples: int = 10,
        use_cql: bool = False,
        use_sde: bool = False,
        sde_sample_freq: int = -1,
        use_sde_at_warmup: bool = False,
        tensorboard_log: Optional[str] = None,
        create_eval_env: bool = False,
        policy_kwargs: Dict[str, Any] = None,
        verbose: int = 0,
        seed: Optional[int] = None,
        device: Union[th.device, str] = "auto",
        _init_setup_model: bool = True,
    ):

        super(SAC, self).__init__(
            policy,
            env,
            SACPolicy,
            learning_rate,
            replay_buffer_class,
            replay_buffer_kwargs,
            buffer_size,
            learning_starts,
            batch_size,
            tau,
            gamma,
            train_freq,
            gradient_steps,
            n_episodes_rollout,
            action_noise,
            policy_kwargs=policy_kwargs,
            tensorboard_log=tensorboard_log,
            verbose=verbose,
            device=device,
            create_eval_env=create_eval_env,
            seed=seed,
            use_sde=use_sde,
            sde_sample_freq=sde_sample_freq,
            use_sde_at_warmup=use_sde_at_warmup,
            optimize_memory_usage=optimize_memory_usage,
        )

        self.target_entropy = target_entropy
        self.log_ent_coef = None  # type: Optional[th.Tensor]
        # Entropy coefficient / Entropy temperature
        # Inverse of the reward scale
        self.ent_coef = ent_coef
        self.target_update_interval = target_update_interval
        self.ent_coef_optimizer = None
        # CQL
        self.use_cql = use_cql
        # TODO: allow constant alpha coeff
        self.initial_alpha = initial_alpha
        self.alpha_threshold = alpha_threshold
        self.n_action_samples = n_action_samples

        if _init_setup_model:
            self._setup_model()

    def _setup_model(self) -> None:
        super(SAC, self)._setup_model()
        self._create_aliases()

        self.replay_buffer.actor = self.actor
        self.replay_buffer.ent_coef = 0.0

        # Target entropy is used when learning the entropy coefficient
        if self.target_entropy == "auto":
            # automatically set target entropy if needed
            self.target_entropy = -np.prod(self.env.action_space.shape).astype(np.float32)
        else:
            # Force conversion
            # this will also throw an error for unexpected string
            self.target_entropy = float(self.target_entropy)

        # The entropy coefficient or entropy can be learned automatically
        # see Automating Entropy Adjustment for Maximum Entropy RL section
        # of https://arxiv.org/abs/1812.05905
        if isinstance(self.ent_coef, str) and self.ent_coef.startswith("auto"):
            # Default initial value of ent_coef when learned
            init_value = 1.0
            if "_" in self.ent_coef:
                init_value = float(self.ent_coef.split("_")[1])
                assert init_value > 0.0, "The initial value of ent_coef must be greater than 0"

            # Note: we optimize the log of the entropy coeff which is slightly different from the paper
            # as discussed in https://github.com/rail-berkeley/softlearning/issues/37
            self.log_ent_coef = th.log(th.ones(1, device=self.device) * init_value).requires_grad_(True)
            self.ent_coef_optimizer = th.optim.Adam([self.log_ent_coef], lr=self.lr_schedule(1))
        else:
            # Force conversion to float
            # this will throw an error if a malformed string (different from 'auto')
            # is passed
            self.ent_coef_tensor = th.tensor(float(self.ent_coef)).to(self.device)

        # CQL
        if self.use_cql:
            self.log_alpha = th.log(th.ones(1, device=self.device) * self.initial_alpha).requires_grad_(True)
            self.alpha_optimizer = th.optim.Adam([self.log_alpha], lr=self.lr_schedule(1))

    def _create_aliases(self) -> None:
        self.actor = self.policy.actor
        self.critic = self.policy.critic
        self.critic_target = self.policy.critic_target

    def update_alpha(self, replay_data: ReplayBufferSamples):
        loss = -self._compute_conservative_loss(replay_data)
        self.alpha_optimizer.zero_grad()
        loss.backward()
        self.alpha_optimizer.step()

    def _compute_conservative_loss(self, replay_data: ReplayBufferSamples):
        # from https://github.com/takuseno/d3rlpy
        obs_t = replay_data.observations
        act_t = replay_data.actions
        action_dim = get_action_dim(self.action_space)
        n_critics = self.critic.n_critics
        assert n_critics == 1
        with th.no_grad():
            policy_actions, n_log_probs = [], []
            for _ in range(self.n_action_samples):
                if self.use_sde:
                    self.actor.reset_noise()
                actions_pi, log_prob = self.actor.action_log_prob(replay_data.observations)
                policy_actions.append(actions_pi)
                n_log_probs.append(log_prob)
            # (batch, n, action_dim)
            policy_actions = th.cat(policy_actions, dim=1).view(len(replay_data.observations), self.n_action_samples, action_dim)
            # assert policy_actions.shape == (len(replay_data.observations), self.n_action_samples, action_dim)
            # (batch, n, 1)
            n_log_probs = th.cat(n_log_probs).view(len(replay_data.observations), self.n_action_samples, 1)
            # assert n_log_probs.shape == (len(replay_data.observations), self.n_action_samples, 1)

        repeated_obs_t = obs_t.expand(self.n_action_samples, *obs_t.shape)
        # (n, batch, obs_dim) -> (batch, n, obs_dim)
        transposed_obs_t = repeated_obs_t.transpose(0, 1)
        # (batch, n, obs_dim) -> (batch * n, obs_dim)
        flat_obs_t = transposed_obs_t.reshape(-1, *obs_t.shape[1:])
        # (batch, n, action_dim) -> (batch * n, action_dim)
        flat_policy_acts = policy_actions.reshape(-1, action_dim)

        # estimate action-values for policy actions
        policy_values = self.critic(flat_obs_t, flat_policy_acts)[0]
        policy_values = policy_values.view(n_critics, obs_t.shape[0], self.n_action_samples, 1)
        log_probs = n_log_probs.view(1, -1, self.n_action_samples, 1)

        # estimate action-values for actions from uniform distribution
        # uniform distribution between [-1.0, 1.0]
        random_actions = th.zeros_like(flat_policy_acts).uniform_(-1.0, 1.0)
        random_values = self.critic(flat_obs_t, random_actions)[0]
        random_values = random_values.view(n_critics, obs_t.shape[0], self.n_action_samples, 1)

        # get maximum value to avoid overflow
        base = th.max(policy_values.max(), random_values.max()).detach()

        # compute logsumexp
        policy_meanexp = (policy_values - base - log_probs).exp().mean(dim=2)
        random_meanexp = (random_values - base).exp().mean(dim=2) / 0.5
        # small constant value seems to be necessary to avoid nan
        logsumexp = (0.5 * random_meanexp + 0.5 * policy_meanexp + 1e-10).log()
        logsumexp += base

        # estimate action-values for data actions
        data_values = self.critic(obs_t, act_t)[0]

        element_wise_loss = logsumexp - data_values - self.alpha_threshold

        # this clipping seems to stabilize training
        clipped_alpha = self.log_alpha.clamp(-10.0, 2.0).exp()

        return (clipped_alpha * element_wise_loss).sum(dim=0).mean()

    def train(self, gradient_steps: int, batch_size: int = 64) -> None:
        # Update optimizers learning rate
        optimizers = [self.actor.optimizer, self.critic.optimizer]
        if self.ent_coef_optimizer is not None:
            optimizers += [self.ent_coef_optimizer]

        if self.use_cql:
            optimizers += [self.alpha_optimizer]

        # Update learning rate according to lr schedule
        self._update_learning_rate(optimizers)

        ent_coef_losses, ent_coefs = [], []
        actor_losses, critic_losses = [], []

        for gradient_step in range(gradient_steps):
            # Sample replay buffer
            replay_data = self.replay_buffer.sample(batch_size, env=self._vec_normalize_env)

            # We need to sample because `log_std` may have changed between two gradient steps
            if self.use_sde:
                self.actor.reset_noise()

            # Action by the current actor for the sampled state
            actions_pi, log_prob = self.actor.action_log_prob(replay_data.observations)
            log_prob = log_prob.reshape(-1, 1)

            ent_coef_loss = None
            if self.ent_coef_optimizer is not None:
                # Important: detach the variable from the graph
                # so we don't change it with other losses
                # see https://github.com/rail-berkeley/softlearning/issues/60
                ent_coef = th.exp(self.log_ent_coef.detach())
                ent_coef_loss = -(self.log_ent_coef * (log_prob + self.target_entropy).detach()).mean()
                ent_coef_losses.append(ent_coef_loss.item())
            else:
                ent_coef = self.ent_coef_tensor

            ent_coefs.append(ent_coef.item())

            self.replay_buffer.ent_coef = ent_coef.item()

            # Optimize entropy coefficient, also called
            # entropy temperature or alpha in the paper
            if ent_coef_loss is not None:
                self.ent_coef_optimizer.zero_grad()
                ent_coef_loss.backward()
                self.ent_coef_optimizer.step()

            with th.no_grad():
                # Select action according to policy
                next_actions, next_log_prob = self.actor.action_log_prob(replay_data.next_observations)
                # Compute the target Q value: min over all critics targets
                targets = th.cat(self.critic_target(replay_data.next_observations, next_actions), dim=1)
                target_q, _ = th.min(targets, dim=1, keepdim=True)
                # add entropy term
                target_q = target_q - ent_coef * next_log_prob.reshape(-1, 1)
                # td error + entropy term
                q_backup = replay_data.rewards + (1 - replay_data.dones) * self.gamma * target_q

            # Get current Q estimates for each critic network
            # using action from the replay buffer
            current_q_estimates = self.critic(replay_data.observations, replay_data.actions)

            # Compute critic loss
            critic_loss = 0.5 * sum([F.mse_loss(current_q, q_backup) for current_q in current_q_estimates])
            critic_losses.append(critic_loss.item())

            # CQL
            if self.use_cql:
                critic_loss += self._compute_conservative_loss(replay_data)

            # Optimize the critic
            self.critic.optimizer.zero_grad()
            critic_loss.backward()
            self.critic.optimizer.step()

            # Compute actor loss
            # Alternative: actor_loss = th.mean(log_prob - qf1_pi)
            # Mean over all critic networks
            q_values_pi = th.cat(self.critic.forward(replay_data.observations, actions_pi), dim=1)
            min_qf_pi, _ = th.min(q_values_pi, dim=1, keepdim=True)
            actor_loss = (ent_coef * log_prob - min_qf_pi).mean()
            actor_losses.append(actor_loss.item())

            # Optimize the actor
            self.actor.optimizer.zero_grad()
            actor_loss.backward()
            self.actor.optimizer.step()

            # CQL
            if self.use_cql:
                self.update_alpha(replay_data)

            # Update target networks
            if gradient_step % self.target_update_interval == 0:
                polyak_update(self.critic.parameters(), self.critic_target.parameters(), self.tau)

        self._n_updates += gradient_steps

        logger.record("train/n_updates", self._n_updates, exclude="tensorboard")
        logger.record("train/ent_coef", np.mean(ent_coefs))
        logger.record("train/actor_loss", np.mean(actor_losses))
        logger.record("train/critic_loss", np.mean(critic_losses))
        if len(ent_coef_losses) > 0:
            logger.record("train/ent_coef_loss", np.mean(ent_coef_losses))
        if self.use_cql:
            logger.record("train/alpha", th.exp(self.log_alpha.detach()).item())

    def pretrain(
        self,
        gradient_steps: int,
        batch_size: int = 64,
        n_action_samples: int = 4,
        target_update_interval: int = 100,
        strategy: str = "binary",
        reduce: str = "mean",
        exp_temperature: float = 1.0,
        off_policy_update_freq: int = -1,
    ) -> None:
        """
        Pretrain with Critic Regularized Regression (CRR)
        Paper: https://arxiv.org/abs/2006.15134
        """
        # Update optimizers learning rate
        optimizers = [self.actor.optimizer, self.critic.optimizer]
        if self.ent_coef_optimizer is not None:
            optimizers += [self.ent_coef_optimizer]

        # Update learning rate according to lr schedule
        self._update_learning_rate(optimizers)

        actor_losses, critic_losses = [], []

        for gradient_step in tqdm(range(gradient_steps)):

            if off_policy_update_freq > 0 and gradient_step % off_policy_update_freq == 0:
                self.train(gradient_steps=1, batch_size=batch_size)
                continue

            # Sample replay buffer
            replay_data = self.replay_buffer.sample(batch_size, env=self._vec_normalize_env)

            # We need to sample because `log_std` may have changed between two gradient steps
            if self.use_sde:
                self.actor.reset_noise()

            # Action by the current actor for the sampled state
            actions_pi, log_prob = self.actor.action_log_prob(replay_data.observations)
            log_prob = log_prob.reshape(-1, 1)

            if self.ent_coef_optimizer is not None:
                # Important: detach the variable from the graph
                # so we don't change it with other losses
                # see https://github.com/rail-berkeley/softlearning/issues/60
                ent_coef = th.exp(self.log_ent_coef.detach())
                ent_coef_loss = -(self.log_ent_coef * (log_prob + self.target_entropy).detach()).mean()
            else:
                ent_coef = self.ent_coef_tensor

            self.replay_buffer.ent_coef = ent_coef.item()

            with th.no_grad():
                # Select action according to policy
                next_actions, next_log_prob = self.actor.action_log_prob(replay_data.next_observations)
                # Compute the target Q value: min over all critics targets
                targets = th.cat(self.critic_target(replay_data.next_observations, next_actions), dim=1)
                target_q, _ = th.min(targets, dim=1, keepdim=True)
                # add entropy term
                target_q = target_q - ent_coef * next_log_prob.reshape(-1, 1)
                # td error + entropy term
                q_backup = replay_data.rewards + (1 - replay_data.dones) * self.gamma * target_q

            # Get current Q estimates for each critic network
            # using action from the replay buffer
            current_q_estimates = self.critic(replay_data.observations, replay_data.actions)

            # Compute critic loss
            critic_loss = 0.5 * sum([F.mse_loss(current_q, q_backup) for current_q in current_q_estimates])
            critic_losses.append(critic_loss.item())

            # Optimize the critic
            self.critic.optimizer.zero_grad()
            critic_loss.backward()
            self.critic.optimizer.step()

            if strategy == "bc":
                # Behavior cloning
                weight = 1
            else:
                with th.no_grad():
                    qf_buffer = th.min(*self.critic(replay_data.observations, replay_data.actions))

                    qf_agg = None
                    for _ in range(n_action_samples):
                        if self.use_sde:
                            self.actor.reset_noise()
                        actions_pi, _ = self.actor.action_log_prob(replay_data.observations)

                        qf_pi = th.min(*self.critic(replay_data.observations, actions_pi.detach()))
                        if qf_agg is None:
                            if reduce == "max":
                                qf_agg = qf_pi
                            else:
                                qf_agg = qf_pi / n_action_samples
                        else:
                            if reduce == "max":
                                qf_agg = th.max(qf_pi, qf_agg)
                            else:
                                qf_agg += qf_pi / n_action_samples

                    advantage = qf_buffer - qf_agg
                if strategy == "binary":
                    # binary advantage
                    weight = advantage > 0
                else:
                    # exp advantage
                    exp_clip = 20.0
                    weight = th.clamp(th.exp(advantage / exp_temperature), 0.0, exp_clip)

            # Log prob by the current actor for the sampled state and action
            log_prob = self.actor.evaluate_actions(replay_data.observations, replay_data.actions)
            log_prob = log_prob.reshape(-1, 1)

            # weigthed regression loss (close to policy gradient loss)
            actor_loss = (-log_prob * weight).mean()
            # actor_loss = ((actions_pi - replay_data.actions * weight) ** 2).mean()
            actor_losses.append(actor_loss.item())

            # Optimize the actor
            self.actor.optimizer.zero_grad()
            actor_loss.backward()
            self.actor.optimizer.step()

            # Hard copy
            if gradient_step % target_update_interval == 0:
                self.critic_target.load_state_dict(self.critic.state_dict())

    def learn(
        self,
        total_timesteps: int,
        callback: MaybeCallback = None,
        log_interval: int = 4,
        eval_env: Optional[GymEnv] = None,
        eval_freq: int = -1,
        n_eval_episodes: int = 5,
        tb_log_name: str = "SAC",
        eval_log_path: Optional[str] = None,
        reset_num_timesteps: bool = True,
    ) -> OffPolicyAlgorithm:

        return super(SAC, self).learn(
            total_timesteps=total_timesteps,
            callback=callback,
            log_interval=log_interval,
            eval_env=eval_env,
            eval_freq=eval_freq,
            n_eval_episodes=n_eval_episodes,
            tb_log_name=tb_log_name,
            eval_log_path=eval_log_path,
            reset_num_timesteps=reset_num_timesteps,
        )

    def excluded_save_params(self) -> List[str]:
        """
        Returns the names of the parameters that should be excluded by default
        when saving the model.

        :return: (List[str]) List of parameters that should be excluded from save
        """
        # Exclude aliases
        return super(SAC, self).excluded_save_params() + ["actor", "critic", "critic_target"]

    def get_torch_variables(self) -> Tuple[List[str], List[str]]:
        """
        cf base class
        """
        state_dicts = ["policy", "actor.optimizer", "critic.optimizer"]
        saved_tensors = ["log_ent_coef"]
        if self.ent_coef_optimizer is not None:
            state_dicts.append("ent_coef_optimizer")
        else:
            saved_tensors.append("ent_coef_tensor")

        if self.use_cql:
            state_dicts.append("alpha_optimizer")
            saved_tensors.append("log_alpha")
        return state_dicts, saved_tensors
