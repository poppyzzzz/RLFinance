# -*- coding:utf-8 -*-
import tensorflow as tf
import numpy as np
import os

# This model was inspired by
# Deep Direct Reinforcement Learning for Financial Signal Representation and Trading

'''
Model interpretation:
    inputs:
    f:  shape=(batch_size, feature_number), take any information you need and make a matrix in n rows and m columns
        n is the timestep for a batch, m is the number of features. Recommend to use technical indicators (MACD,RSI...)
        of assets you want to manage.
    z:  return of rate vector with n elements
    c:  transaction cost

    formulas:
    d_t = g(f,d_t-1...d_t-n) where g is the complex non-linear transformation procedure, here we use GRU-rnn
        Here, d_t is the action, represent the predict portfolio weight generated by current information
        and previous several actions
    r_t = d_t-1*log(z_t) -c*|d_t-d_t-1|
        r_t is the return of current time step, which is calculated by using the log of the return of rate of assets price in current step
        with previous predict action d_t-1. Then, subtract transaction cost if the weight of holding assets
        changes.
    R = \sum_t(r_t)
        The total log return
    object: max(R|theta)
        The objective is to maximize the total return.
'''


class DRL_PairsTrading(object):
    def __init__(self, feature_number, object_function= 'sortino', dense_units_list=[1024, 768, 512, 256], rnn_hidden_layer_number=4, rnn_hidden_units_number=128, learning_rate=0.001):
        tf.reset_default_graph()
        self.f = tf.placeholder(dtype=tf.float32, shape=[None, feature_number], name='environment_features')
        self.z = tf.placeholder(dtype=tf.float32, shape=[None, 1], name='environment_return')
        self.c = tf.placeholder(dtype=tf.float32, shape=[], name='environment_fee')
        self.dropout_keep_prob = tf.placeholder(dtype=tf.float32, shape=[], name='dropout_keep_prob')
        self.hidden_rnn_init_state = tf.placeholder(tf.float32, [rnn_hidden_layer_number, 1, rnn_hidden_units_number], name='hidden_rnn_initial_state')
        self._state_per_layer_list = tf.unstack(self.hidden_rnn_init_state, axis=0)
        self.rnn_tuple_state = tuple(self._state_per_layer_list)
        self.previous_rnn_output = tf.placeholder(dtype=tf.float32, shape=[1, rnn_hidden_units_number], name='previous_rnn_output')
        
        with tf.variable_scope('feed_forward', initializer=tf.contrib.layers.xavier_initializer(uniform=False), regularizer=tf.contrib.layers.l2_regularizer(0.01)):
            self.dense_output = self.f
            for u_number in dense_units_list:
                self.dense_output = self._add_dense_layer(self.dense_output, output_shape=u_number, drop_keep_prob=self.dropout_keep_prob)
            self.dense_output = self._add_dense_layer(self.dense_output, output_shape=rnn_hidden_units_number, drop_keep_prob=self.dropout_keep_prob)
        
        with tf.variable_scope('rnn', initializer=tf.contrib.layers.xavier_initializer(uniform=False), regularizer=tf.contrib.layers.l2_regularizer(0.01)):
            rnn_hidden_cells = [self._add_gru_cell(rnn_hidden_units_number)] * rnn_hidden_layer_number
            layered_cell = tf.contrib.rnn.MultiRNNCell(rnn_hidden_cells)
            self.zero_state = layered_cell.zero_state(1, dtype=tf.float32)
            rnn_input = tf.expand_dims(self.dense_output, axis=0)
            self.rnn_outputs, self.current_state = tf.nn.dynamic_rnn(layered_cell, initial_state=self.rnn_tuple_state, inputs=rnn_input)
            self.current_output = tf.reshape(self.rnn_outputs[0][-1], [1, rnn_hidden_units_number])
            self.rnn_outputs = tf.concat((self.previous_rnn_output, tf.unstack(self.rnn_outputs, axis=0)[0]), axis=0)
        with tf.variable_scope('action', initializer=tf.contrib.layers.xavier_initializer(uniform=False), regularizer=tf.contrib.layers.l2_regularizer(0.01)):
            self.action = tf.contrib.layers.fully_connected(num_outputs=1, inputs=self.rnn_outputs, activation_fn=tf.tanh)
        with tf.variable_scope('reward'):
            self.log_reward_t = tf.log(self.z) * self.action[:-1] - self.c * tf.abs(self.action[1:] - self.action[:-1])
            self.cum_log_reward = tf.reduce_sum(self.log_reward_t)
            self.reward_t = tf.exp(self.log_reward_t)
            self.cum_reward = tf.reduce_prod(self.reward_t)
            self.sortino = self._sortino_ratio(self.log_reward_t, 0)
            self.sharpe = self._sharpe_ratio(self.log_reward_t, 0)
        with tf.variable_scope('train'):
            optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate)
            if object_function == 'reward':
                self.train_op = optimizer.minimize(-self.cum_log_reward)
            elif object_function == 'sharpe':
                self.train_op = optimizer.minimize(-self.sharpe)
            else:
                self.train_op = optimizer.minimize(-self.sortino)
        self.init_op = tf.global_variables_initializer()
        self.saver = tf.train.Saver()
        self.session = tf.Session()
    
    def init_model(self):
        self.session.run(self.init_op)
    
    def get_rnn_zero_state(self):
        zero_states = self.session.run([self.zero_state])[0]
        return zero_states

    def _sortino_ratio(self, r, rf):
        mean, var = tf.nn.moments(r, axes=[0])
        sign = tf.sign(-tf.sign(r - rf) + 1)
        number = tf.reduce_sum(sign)
        lower = sign * r
        square_sum = tf.reduce_sum(tf.pow(lower, 2))
        sortino_var = tf.sqrt(square_sum / number)
        sortino = (mean - rf) / sortino_var
        return sortino

    def _sharpe_ratio(self, r, rf):
        mean, var = tf.nn.moments(r - rf, axes=[0])
        return mean / var
    
    def _add_dense_layer(self, inputs, output_shape, drop_keep_prob, act=tf.nn.tanh):
        output = tf.contrib.layers.fully_connected(activation_fn=act, num_outputs=output_shape, inputs=inputs)
        output = tf.nn.dropout(output, drop_keep_prob)
        return output
    
    def _add_gru_cell(self, units_number):
        return tf.contrib.rnn.GRUCell(num_units=units_number)
    
    def build_feed_dict(self, batch_F, batch_Z, keep_prob, fee, rnn_hidden_init_state, previous_output):
        return {
            self.f: batch_F,
            self.z: batch_Z,
            self.dropout_keep_prob: keep_prob,
            self.hidden_rnn_init_state: rnn_hidden_init_state,
            self.previous_rnn_output: previous_output,
            self.c: fee
        }

    def change_drop_keep_prob(self, feed_dict, new_prob):
        feed_dict[self.dropout_keep_prob] = new_prob
        return feed_dict
    
    def train(self, feed):
        self.session.run([self.train_op], feed_dict=feed)
    
    def load_model(self, model_file='./trade_model_checkpoint/trade_model'):
        self.saver.restore(self.session, model_file)
    
    def save_model(self, model_path='./trade_model_checkpoint'):
        if not os.path.exists(model_path):
            os.mkdir(model_path)
        model_file = model_path + '/trade_model'
        self.saver.save(self.session, model_file)
    
    def trade(self, feed):
        rewards, cum_reward, actions, current_state, current_rnn_output = self.session.run([self.log_reward_t, self.cum_log_reward, self.action, self.current_state, self.current_output], feed_dict=feed)
        return rewards, cum_reward, actions, current_state, current_rnn_output
