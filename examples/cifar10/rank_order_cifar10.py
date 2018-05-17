import os
import sys
import torch
import numpy             as np
import argparse
import matplotlib.pyplot as plt

from bindsnet import *
from time     import time as t

def get_square_weights(weights, n_sqrt, side):
	square_weights = torch.zeros_like(torch.Tensor(side * n_sqrt, side * n_sqrt))
	for i in range(n_sqrt):
		for j in range(n_sqrt):
			if not i * n_sqrt + j < weights.size(1):
				break
			
			fltr = weights[:, i * n_sqrt + j].contiguous().view(side, side)
			square_weights[i * side : (i + 1) * side, (j % n_sqrt) * side : ((j % n_sqrt) + 1) * side] = fltr
	
	return square_weights

def get_square_assignments(assignments, n_sqrt):
	square_assignments = -1 * torch.ones_like(torch.Tensor(n_sqrt, n_sqrt))
	for i in range(n_sqrt):
		for j in range(n_sqrt):
			if not i * n_sqrt + j < assignments.size(0):
				break
			
			assignment = assignments[i * n_sqrt + j]
			square_assignments[i : (i + 1), (j % n_sqrt) : ((j % n_sqrt) + 1)] = assignments[i * n_sqrt + j]
	
	return square_assignments

print()

parser = argparse.ArgumentParser()
parser.add_argument('--seed', type=int, default=0)
parser.add_argument('--n_neurons', type=int, default=100)
parser.add_argument('--n_train', type=int, default=60000)
parser.add_argument('--n_test', type=int, default=10000)
parser.add_argument('--exc', type=float, default=22.5)
parser.add_argument('--inh', type=float, default=17.5)
parser.add_argument('--time', type=int, default=25)
parser.add_argument('--dt', type=int, default=1.0)
parser.add_argument('--progress_interval', type=int, default=10)
parser.add_argument('--update_interval', type=int, default=100)
parser.add_argument('--train', dest='train', action='store_true')
parser.add_argument('--test', dest='train', action='store_false')
parser.add_argument('--plot', dest='plot', action='store_true')
parser.add_argument('--gpu', dest='gpu', action='store_true')
parser.set_defaults(plot=False, gpu=False, train=True)

locals().update(vars(parser.parse_args()))

if gpu and torch.cuda.is_available():
	torch.set_default_tensor_type('torch.cuda.FloatTensor')
	torch.cuda.manual_seed_all(seed)
else:
	torch.manual_seed(seed)

if not train:
	update_interval = n_test

n_sqrt = int(np.ceil(np.sqrt(n_neurons)))
path = os.path.join('..', '..', 'data', 'CIFAR10')
	
# Build network.
network = DiehlAndCook(n_inpt=32*32*3,
					   n_neurons=n_neurons,
					   exc=exc,
					   inh=inh,
					   time=time,
					   dt=dt,
					   nu_pre=0,
					   nu_post=1,
					   wmin=0,
					   wmax=10)

# Initialize data "environment".
environment = DatasetEnvironment(dataset=CIFAR10(path=path),
								 train=train,
								 time=time)

# Specify data encoding and network to environment feedback.
encoding = rank_order
feedback = no_feedback

spikes = {}
for layer in set(network.layers):
	spikes[layer] = Monitor(network.layers[layer], state_vars=['s'], time=time)
	network.add_monitor(spikes[layer], name='%s_spikes' % layer)

voltages = {}
for layer in set(network.layers) - {'X'}:
	voltages[layer] = Monitor(network.layers[layer], state_vars=['v'], time=time)
	network.add_monitor(voltages[layer], name='%s_voltages' % layer)

# Build pipeline from above-specified components.
pipeline = Pipeline(network=network,
					environment=environment,
					encoding=encoding,
					feedback=feedback,
					plot=plot,
					time=time,
				    plot_interval=1)

# Neuron assignments and spike proportions.
assignments = -torch.ones_like(torch.Tensor(n_neurons))
proportions = torch.zeros_like(torch.Tensor(n_neurons, 10))
rates = torch.zeros_like(torch.Tensor(n_neurons, 10))

# Record spikes during the simulation.
spike_record = torch.zeros(update_interval, time, n_neurons)

# Get data labels.
labels = pipeline.env.labels

# Sequence of accuracy estimates.
accuracy = {'all' : [], 'proportion' : []}

# Train the network.
print('Begin training.\n')
start = t()

for i in range(n_train):
	if i % progress_interval == 0:
		print('Progress: %d / %d (%.4f seconds)' % (i, n_train, t() - start))
		start = t()
	
	if i % update_interval == 0 and i > 0:
		# Get network predictions.
		all_activity_pred = all_activity(spike_record, assignments, 10)
		proportion_pred = proportion_weighting(spike_record, assignments, proportions, 10)

		# Compute network accuracy according to available classification strategies.
		accuracy['all'].append(100 * torch.sum(labels[i - update_interval:i].long() == all_activity_pred) / update_interval)
		accuracy['proportion'].append(100 * torch.sum(labels[i - update_interval:i].long() == proportion_pred) / update_interval)

		print('\nAll activity accuracy: %.2f (last), %.2f (average), %.2f (best)' \
						% (accuracy['all'][-1], np.mean(accuracy['all']), np.max(accuracy['all'])))
		print('Proportion weighting accuracy: %.2f (last), %.2f (average), %.2f (best)\n' \
						% (accuracy['proportion'][-1], np.mean(accuracy['proportion']),
						  np.max(accuracy['proportion'])))

		# Assign labels to excitatory layer neurons.
		assignments, proportions, rates = assign_labels(spike_record, labels[i - update_interval:i], 10, rates)
	
	pipeline.step()
	
	# Add to spikes recording.
	spike_record[i % update_interval] = spikes['Ae'].get('s').t()
	
	# Normalize input -> excitatory weights
	pipeline.normalize(source='X', target='Ae', norm=3500.0)
	
	# Optionally plot various simulation info.
	if plot:
		if gpu:
			image = pipeline.obs.view(3, 32, 32).cpu().numpy().transpose(1, 2, 0)
			inpt = pipeline.encoded.view(time, 3*32*32).sum(0).view(3, 32, 32).sum(0).float().cpu().numpy()
			weights = network.connections[('X', 'Ae')].w.view(3, 32, 32, n_neurons).cpu().numpy()
		else:
			image = pipeline.obs.view(3, 32, 32).numpy().transpose(1, 2, 0)
			inpt = pipeline.encoded.view(time, 3*32*32).sum(0).view(3, 32, 32).sum(0).float().numpy()
			weights = network.connections[('X', 'Ae')].w.view(3, 32, 32, n_neurons).numpy()
		
		weights = weights.transpose(1, 2, 0, 3).sum(2).reshape(32*32, n_neurons)
		weights = torch.from_numpy(weights)
			
		square_assignments = get_square_assignments(assignments, n_sqrt)
		square_weights = get_square_weights(weights, n_sqrt, 32)
		
		if i == 0:
			inpt_axes, inpt_ims = plot_input(image, inpt, label=labels[i])
			assigns_im = plot_assignments(square_assignments)
			perf_ax = plot_performance(accuracy)
			weights_ax = plot_weights(square_weights, wmin=0.0, wmax=10.0)
		else:
			inpt_axes, inpt_ims = plot_input(image, inpt, label=labels[i], axes=inpt_axes, ims=inpt_ims)
			assigns_im = plot_assignments(square_assignments, im=assigns_im)
			perf_ax = plot_performance(accuracy, ax=perf_ax)
			weights_im = plot_weights(square_weights, im=weights_ax)
		
		plt.pause(1e-8)
	
	network._reset()  # Reset state variables.

print('Progress: %d / %d (%.4f seconds)\n' % (n_train, n_train, t() - start))
print('Training complete.\n')