import argparse
import datetime
import os

from joblib import Parallel, delayed
import numpy as np
import tensorflow as tf

from mushroom.core.core import Core
from mushroom.environments import *
from mushroom.utils.dataset import compute_J, compute_scores

from dqn import DQN, DoubleDQN, WeightedDQN
from prepro import OneHot
from policy import BootPolicy
from simple_net import SimpleNet


"""
This script can be used to run Atari experiments with DQN.

"""

# Disable tf cpp warnings
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"


def print_epoch(epoch):
    print '################################################################'
    print 'Epoch: ', epoch
    print '----------------------------------------------------------------'


def get_stats(dataset, name):
    if name == 'grid':
        abs_count = 0
        rewards = list()
        for d in dataset:
            rewards.append(d[2])
            abs_count += d[4]
        print('Goal reached: %d' % abs_count)

        return rewards
    else:
        J = np.mean(compute_J(dataset, 1.))
        print('J: %f' % J)

        return J


def experiment(algorithm):
    np.random.seed()

    # Argument parser
    parser = argparse.ArgumentParser()

    arg_game = parser.add_argument_group('Game')
    arg_game.add_argument("--name",
                          type=str,
                          default='grid',
                          help='Gym ID of the Atari game.')

    arg_mem = parser.add_argument_group('Replay Memory')
    arg_mem.add_argument("--initial-replay-size", type=int, default=50000,
                         help='Initial size of the replay memory.')
    arg_mem.add_argument("--max-replay-size", type=int, default=500000,
                         help='Max size of the replay memory.')

    arg_net = parser.add_argument_group('Deep Q-Network')
    arg_net.add_argument("--optimizer",
                         choices=['adadelta',
                                  'adam',
                                  'rmsprop',
                                  'rmspropcentered'],
                         default='adam',
                         help='Name of the optimizer to use to learn.')
    arg_net.add_argument("--learning-rate", type=float, default=.00025,
                         help='Learning rate value of the optimizer. Only used'
                              'in rmspropcentered')
    arg_net.add_argument("--decay", type=float, default=.95,
                         help='Discount factor for the history coming from the'
                              'gradient momentum in rmspropcentered')
    arg_net.add_argument("--epsilon", type=float, default=.01,
                         help='Epsilon term used in rmspropcentered')

    arg_alg = parser.add_argument_group('Algorithm')
    arg_alg.add_argument("--n-approximators", type=int, default=10,
                         help="Number of approximators used in the ensemble for"
                              "Averaged DQN.")
    arg_alg.add_argument("--batch-size", type=int, default=32,
                         help='Batch size for each fit of the network.')
    arg_alg.add_argument("--history-length", type=int, default=4,
                         help='Number of frames composing a state.')
    arg_alg.add_argument("--target-update-frequency", type=int, default=10000,
                         help='Number of learning step before each update of'
                              'the target network.')
    arg_alg.add_argument("--evaluation-frequency", type=int, default=250000,
                         help='Number of learning step before each evaluation.'
                              'This number represents an epoch.')
    arg_alg.add_argument("--train-frequency", type=int, default=4,
                         help='Number of learning steps before each fit of the'
                              'neural network.')
    arg_alg.add_argument("--max-steps", type=int, default=50000000,
                         help='Total number of learning steps.')
    arg_alg.add_argument("--test-samples", type=int, default=125000,
                         help='Number of steps for each evaluation.')
    arg_alg.add_argument("--max-no-op-actions", type=int, default=30,
                         help='Maximum number of no-op action performed at the'
                              'beginning of the episodes. The minimum number is'
                              'history_length.')
    arg_alg.add_argument("--no-op-action-value", type=int, default=0,
                         help='Value of the no-op action.')
    arg_alg.add_argument("--p-mask", type=float, default=2 / 3.)

    arg_utils = parser.add_argument_group('Utils')
    arg_utils.add_argument('--load-path', type=str,
                           help='Path of the model to be loaded.')
    arg_utils.add_argument('--save', action='store_true',
                           help='Flag specifying whether to save the model.')
    arg_utils.add_argument('--render', action='store_true',
                           help='Flag specifying whether to render the game.')
    arg_utils.add_argument('--quiet', action='store_true',
                           help='Flag specifying whether to hide the progress'
                                'bar.')
    arg_utils.add_argument('--debug', action='store_true',
                           help='Flag specifying whether the script has to be'
                                'run in debug mode.')

    args = parser.parse_args()

    scores = list()

    # Evaluation of the model provided by the user.
    if args.load_path:
        if args.name == 'grid':
            mdp = GridWorldGenerator('grid.txt')
        else:
            mdp = Gym(args.name, 200, .99)

        # Policy
        pi = BootPolicy(args.n_approximators)

        # Approximator
        if args.name == 'grid':
            input_shape = (mdp.info.observation_space.n,)
            input_preprocessor = [OneHot(mdp.info.observation_space.n)]
        else:
            input_shape = mdp.info.observation_space.shape
            input_preprocessor = list()
        approximator_params = dict(
            input_shape=input_shape,
            output_shape=(mdp.info.action_space.n,),
            n_actions=mdp.info.action_space.n,
            n_features=80,
            n_approximators=args.n_approximators,
            input_preprocessor=input_preprocessor,
            name='test',
            load_path=args.load_path,
            optimizer={'name': args.optimizer,
                       'lr': args.learning_rate,
                       'decay': args.decay,
                       'epsilon': args.epsilon}
        )

        approximator = SimpleNet

        # Agent
        algorithm_params = dict(
            max_replay_size=0,
            remove_history=True if args.name != 'grid' else False,
            n_approximators=args.n_approximators,
            history_length=args.history_length,
            clip_reward=False,
            max_no_op_actions=args.max_no_op_actions,
            no_op_action_value=args.no_op_action_value,
            p_mask=args.p_mask
        )
        fit_params = dict()
        agent_params = {'approximator_params': approximator_params,
                        'algorithm_params': algorithm_params,
                        'fit_params': fit_params}
        agent = DQN(approximator, pi, mdp.info, agent_params)

        # Algorithm
        core_test = Core(agent, mdp)

        # Evaluate model
        pi.set_eval(True)
        dataset = core_test.evaluate(n_steps=args.test_samples,
                                     render=args.render,
                                     quiet=args.quiet)
        get_stats(dataset, args.name)
    else:
        # DQN learning run

        # Summary folder
        folder_name = './logs/' + algorithm + '/' +\
                      datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S.%f')

        # Settings
        if args.debug:
            initial_replay_size = 50
            max_replay_size = 500
            train_frequency = 5
            target_update_frequency = 10
            test_samples = 20
            evaluation_frequency = 50
            max_steps = 1000
        else:
            initial_replay_size = args.initial_replay_size
            max_replay_size = args.max_replay_size
            train_frequency = args.train_frequency
            target_update_frequency = args.target_update_frequency
            test_samples = args.test_samples
            evaluation_frequency = args.evaluation_frequency
            max_steps = args.max_steps

        # MDP
        if args.name == 'grid':
            mdp = GridWorldGenerator('grid.txt')
        else:
            mdp = Gym(args.name, 200, .99)

        # Policy
        pi = BootPolicy(args.n_approximators)

        # Approximator
        if args.name == 'grid':
            input_shape = (mdp.info.observation_space.n,)
            input_preprocessor = [OneHot(mdp.info.observation_space.n)]
        else:
            input_shape = mdp.info.observation_space.shape
            input_preprocessor = list()
        approximator_params = dict(
            input_shape=input_shape,
            output_shape=(mdp.info.action_space.n,),
            n_actions=mdp.info.action_space.n,
            n_features=80,
            n_approximators=args.n_approximators,
            input_preprocessor=input_preprocessor,
            folder_name=folder_name,
            optimizer={'name': args.optimizer,
                       'lr': args.learning_rate,
                       'decay': args.decay,
                       'epsilon': args.epsilon}
        )

        approximator = SimpleNet

        # Agent
        algorithm_params = dict(
            batch_size=args.batch_size,
            initial_replay_size=initial_replay_size,
            max_replay_size=max_replay_size,
            remove_history=True if args.name != 'grid' else False,
            history_length=args.history_length,
            clip_reward=False,
            n_approximators=args.n_approximators,
            train_frequency=train_frequency,
            target_update_frequency=target_update_frequency,
            max_no_op_actions=args.max_no_op_actions,
            no_op_action_value=args.no_op_action_value,
            p_mask=args.p_mask
        )
        fit_params = dict()
        agent_params = {'approximator_params': approximator_params,
                        'algorithm_params': algorithm_params,
                        'fit_params': fit_params}

        if algorithm == 'dqn':
            agent = DQN(approximator, pi, mdp.info, agent_params)
        elif algorithm == 'ddqn':
            agent = DoubleDQN(approximator, pi, mdp.info, agent_params)
        elif algorithm == 'wdqn':
            agent = WeightedDQN(approximator, pi, mdp.info, agent_params)

        # Algorithm
        core = Core(agent, mdp)
        core_test = Core(agent, mdp)

        # RUN

        # Fill replay memory with random dataset
        print_epoch(0)
        core.learn(n_steps=initial_replay_size,
                   n_steps_per_fit=initial_replay_size, quiet=args.quiet)

        if args.save:
            agent.approximator.model.save()

        # Evaluate initial policy
        pi.set_eval(True)
        dataset = core_test.evaluate(n_steps=test_samples,
                                     render=args.render,
                                     quiet=args.quiet)
        scores.append(get_stats(dataset, args.name))
        pi.set_eval(False)

        np.save(folder_name + '/scores.npy', scores)
        for n_epoch in xrange(1, max_steps / evaluation_frequency + 1):
            print_epoch(n_epoch)
            print '- Learning:'
            # learning step
            core.learn(n_steps=evaluation_frequency,
                       n_steps_per_fit=train_frequency,
                       quiet=args.quiet)

            if args.save:
                agent.approximator.model.save()

            print '- Evaluation:'
            # evaluation step
            core_test.reset()
            pi.set_eval(True)
            dataset = core_test.evaluate(n_steps=test_samples,
                                         render=args.render,
                                         quiet=args.quiet)
            scores.append(get_stats(dataset, args.name))
            pi.set_eval(False)

            np.save(folder_name + '/scores.npy', scores)

    return scores


if __name__ == '__main__':
    algs = ['dqn', 'ddqn', 'wdqn']
    n_experiments = 10

    for a in algs:
        out = Parallel(n_jobs=-1)(
            delayed(experiment)(a) for _ in xrange(n_experiments))
        tf.reset_default_graph()

        np.save('logs/' + a + '/scores.npy', out)