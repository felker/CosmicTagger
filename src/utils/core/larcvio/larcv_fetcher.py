import os


from . import data_transforms
from . import io_templates
import tempfile

import numpy


class larcv_fetcher(object):

    FULL_RESOLUTION_H = 1280
    FULL_RESOLUTION_W = 2048

    def __init__(self, mode, distributed, downsample, dataformat, synthetic, sparse, seed=None):

        if mode not in ['train', 'inference', 'iotest']:
            raise Exception("Larcv Fetcher can't handle mode ", mode)

        if not synthetic:

            if distributed:
                from larcv import distributed_queue_interface
                self._larcv_interface = distributed_queue_interface.queue_interface()
            else:
                from larcv import queueloader
                if mode == "inference":
                    self._larcv_interface = queueloader.queue_interface(
                        random_access_mode="serial_access", seed=seed)
                elif mode == "train" or mode == "iotest":
                    self._larcv_interface = queueloader.queue_interface(
                        random_access_mode="random_blocks", seed=seed)
                else:
                    # Must be synthetic
                    self._larcv_interface = None

        self.mode       = mode
        self.downsample = downsample
        self.dataformat = dataformat
        self.synthetic  = synthetic
        self.sparse     = sparse

        # Compute the realized image shape:
        self.full_image_shape = [self.FULL_RESOLUTION_H, self.FULL_RESOLUTION_W]
        self.ds = 2**downsample

        self.image_shape = [ int(i / self.ds) for i in self.full_image_shape ]


    def image_shape(self):
        '''Return the input shape to the networks (no batch size)'''
        return self.image_shape

    def batch_dims(self, batch_size):

        if self.dataformat == "channels_first":
            shape = [batch_size, 3, self.image_shape[0], self.image_shape[1]]
        else:
            shape = [batch_size, self.image_shape[0], self.image_shape[1], 3]

        return shape

    def prepare_cosmic_sample(self, name, input_file, batch_size, color=None):

        if self.synthetic:
            self.synthetic_index = 0
            self.batch_size = batch_size
            shape = self.batch_dims(24)

            self.synthetic_images = numpy.random.random_sample(shape).astype(numpy.float32)
            self.synthetic_labels = numpy.random.randint(low=0, high=3, size=shape)

        else:
            config = io_templates.dataset_io(
                    input_file  = input_file,
                    name        = name,
                    compression = self.downsample)


            # Generate a named temp file:
            main_file = tempfile.NamedTemporaryFile(mode='w', delete=False)
            main_file.write(config.generate_config_str())

            main_file.close()

            io_config = {
                'filler_name' : config._name,
                'filler_cfg'  : main_file.name,
                'verbosity'   : 5,
                'make_copy'   : False
            }

            # Build up the data_keys:
            data_keys = {
                'image': name + 'data',
                'label': name + 'label'
                }

            self._larcv_interface.prepare_manager(name, io_config, batch_size, data_keys, color=color)
            os.unlink(main_file.name)

            # This queues up the next data
            self._larcv_interface.prepare_next(name)

            return self._larcv_interface.size(name)


    def fetch_next_batch(self, name, force_pop=False):


        if not self.synthetic:
            metadata=True

            pop = True
            if not force_pop:
                pop = False


            minibatch_data = self._larcv_interface.fetch_minibatch_data(name,
                pop=pop,fetch_meta_data=metadata)
            minibatch_dims = self._larcv_interface.fetch_minibatch_dims(name)

            # This brings up the current data
            self._larcv_interface.prepare_next(name)

            for key in minibatch_data:
                if key == 'entries' or key == 'event_ids':
                    continue
                minibatch_data[key] = numpy.reshape(minibatch_data[key], minibatch_dims[key])


            if not self.sparse:
                minibatch_data['image']  = data_transforms.larcvsparse_to_dense_2d(
                    minibatch_data['image'],
                    dense_shape =self.image_shape,
                    dataformat  =self.dataformat)
            else:
                minibatch_data['image']  = data_transforms.larcvsparse_to_scnsparse_2d(
                    minibatch_data['image'])

            # Label is always dense:
            minibatch_data['label']  = data_transforms.larcvsparse_to_dense_2d(
                minibatch_data['label'],
                dense_shape =self.image_shape,
                dataformat  =self.dataformat)



        else:

            minibatch_data = {}
            if self.synthetic_index + self.batch_size > len(self.synthetic_images):
                self.synthetic_index = 0

            lower_index = self.synthetic_index
            upper_index = self.synthetic_index + self.batch_size

            minibatch_data['image']  = self.synthetic_images[lower_index:upper_index]
            minibatch_data['label']  = self.synthetic_labels[lower_index:upper_index]

            self.synthetic_index += 1

        return minibatch_data
