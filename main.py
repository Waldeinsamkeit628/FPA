import argparse
import os
import random
import shutil
import time
import warnings
import h5py
import numpy as np

import torch
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.distributed as dist
import torch.optim
import torch.utils.data
import torch.utils.data.distributed

import sys
import torchvision.transforms as transforms
import datasets
import models
from utils import *
from time import gmtime, strftime
import torchvision


model_names = sorted(name for name in models.__dict__
                     if name.islower() and not name.startswith("__")
                     and callable(models.__dict__[name]))

parser = argparse.ArgumentParser(description='PyTorch ImageNet Training')
parser.add_argument('--data', '-d', metavar='DATA', default='cub',
                    help='dataset')
parser.add_argument('--arch', '-a', metavar='ARCH', default='resnet18',
                    choices=model_names,
                    help='model architecture: ' +
                         ' | '.join(model_names) +
                         ' (default: resnet18)')
parser.add_argument('--backbone', default='resnet18', help='backbone')
parser.add_argument('--save_path', '-s', metavar='SAVE', default='',
                    help='saving path')
parser.add_argument('-j', '--workers', default=3, type=int, metavar='N',
                    help='number of data loading workers (default: 4)')
parser.add_argument('--epochs', default=90, type=int, metavar='N',
                    help='number of total epochs to run')
parser.add_argument('--start-epoch', default=0, type=int, metavar='N',
                    help='manual epoch number (useful on restarts)')
parser.add_argument('-b', '--batch-size', default=256, type=int,
                    metavar='N', help='mini-batch size (default: 256)')
parser.add_argument('--lr', default=0.005, type=float,
                    metavar='LR', help='initial learning rate')
parser.add_argument('--lr1', default=0.1, type=float,
                    metavar='LR', help='initial learning rate')
parser.add_argument('--lr2', default=0.001, type=float,
                    metavar='LR', help='initial learning rate')
parser.add_argument('--epoch_decay', default=30, type=int,
                    metavar='LR', help='initial learning rate')
parser.add_argument('--momentum', default=0.9, type=float, metavar='M',
                    help='momentum')
parser.add_argument('--weight-decay', '--wd', default=1e-4, type=float,
                    metavar='W', help='weight decay (default: 1e-4)')
parser.add_argument('--print-freq', '-p', default=10, type=int,
                    metavar='N', help='print frequency (default: 10)')
parser.add_argument('--resume', default='', type=str, metavar='PATH',
                    help='path to latest checkpoint (default: none)')
parser.add_argument('--pretrained', dest='pretrained', action='store_true',
                    help='use pre-trained model')
parser.add_argument('--world-size', default=1, type=int,
                    help='number of distributed processes')
parser.add_argument('--dist-url', default='tcp://224.66.41.62:23456', type=str,
                    help='url used to set up distributed training')
parser.add_argument('--dist-backend', default='gloo', type=str,
                    help='distributed backend')
parser.add_argument('--seed', default=None, type=int,
                    help='seed for initializing training. ')
parser.add_argument('--gpu', default=None, type=int,
                    help='GPU id to use.')
parser.add_argument('--is_fix', dest='is_fix', action='store_true',
                    help='is_fix.')
''' data proc '''
parser.add_argument('--flippingtest', dest='flippingtest', action='store_true',
                    help='flipping test.')
parser.add_argument('--aug', default='v7', type=str,
                    help='train augmentation')
''' loss '''
parser.add_argument('--sigma', dest='sigma', default=0.5, type=float,
                    help='sigma.')

parser.add_argument('--att', dest='att', default=312, type=int,
                    help='')
parser.add_argument('--lossw', default=1, type=float,
                    help='L_fft weight.')
parser.add_argument('--golibw', default=0.02, type=float,
                    help='global filter weight.')
parser.add_argument('--phasew', default=0.1, type=float,
                    help='')
parser.add_argument('--odr', default=0, type=int,
                    help='')
parser.add_argument("--LB", type=float, default=0.1, help="beta for FDA")
parser.add_argument("--ratio", type=float, default=0.1, help="radius_ratio")
parser.add_argument("--spacew", type=float, default=0.5, help="spaceweight")
best_prec1 = 0


def main():
    global args, best_prec1
    args = parser.parse_args()
    print(args)

    ''' save path '''
    if not os.path.exists(args.save_path):
        os.makedirs(args.save_path)

    ''' random seed '''
    if args.seed is not None:
        random.seed(args.seed)
    else:
        args.seed = random.randint(1, 10000)

    torch.manual_seed(args.seed)
    cudnn.deterministic = True
    print('==> random seed:', args.seed)

    out_dir = '/zero-shot/{}/lr{}_ph{}/_b-{}_lossw-{}_phasew-{}_lr1-{}_lr2-{}_decay-{}_seed-{}'.format(
        args.data,
        args.lr,
        args.phasew,
        args.batch_size,
        args.lossw,
        args.phasew,
        args.lr1,
        args.lr2,
        args.epoch_decay,
        args.seed, )
    os.makedirs(out_dir, exist_ok=True)
    print("The output dictionary is {}".format(out_dir))
    log_dir = out_dir + '/log{}.txt'.format(args.data)
    with open(log_dir, 'w') as f:
        f.write('Training Start:')
        f.write(strftime("%a, %d %b %Y %H:%M:%S +0000", gmtime()) + '\n')
        f.write(args.save_path)

    if args.gpu is not None:
        warnings.warn('You have chosen a specific GPU. This will completely '
                      'disable data parallelism.')

    args.distributed = args.world_size > 1

    if args.distributed:
        dist.init_process_group(backend=args.dist_backend, init_method=args.dist_url,
                                world_size=args.world_size)

    ''' data load info '''
    data_info = h5py.File(os.path.join('./data', args.data, 'data_info.h5'), 'r')
    img_path = str(data_info['img_path'][...]).replace("b'", '').replace("'", '')
    print("1 ******************************")
    print(img_path)
    nc = data_info['all_att'][...].shape[0]
    sf_size = data_info['all_att'][...].shape[1]
    semantic_data = {'seen_class': data_info['seen_class'][...],
                     'unseen_class': data_info['unseen_class'][...],
                     'all_class': np.arange(nc),
                     'all_att': data_info['all_att'][...]}
    ''' load semantic data'''
    args.num_classes = nc
    args.sf_size = sf_size
    args.sf = semantic_data['all_att']

    adj = adj_matrix(nc)
    args.adj = adj

    ''' model building '''
    if args.pretrained:
        print("=> using pre-trained model '{}'".format(args.arch))
        best_prec1 = 0
        model, criterion = models.__dict__[args.arch](pretrained=True, args=args)
    else:
        print("=> creating model '{}'".format(args.arch))
        model, criterion = models.__dict__[args.arch](args=args)
    print("=> is the backbone fixed: '{}'".format(args.is_fix))

    if args.gpu is not None:
        model = model.cuda(args.gpu)
    elif args.distributed:
        model.cuda()
        model = torch.nn.parallel.DistributedDataParallel(model)
    else:
        if args.arch.startswith('alexnet') or args.arch.startswith('vgg'):
            model.features = torch.nn.DataParallel(model.features)
            model.cuda()
        else:
            model = torch.nn.DataParallel(model).cuda()
    criterion = criterion.cuda(args.gpu)

    ''' optimizer '''
    odr_params = [v for k, v in model.named_parameters() if 'odr_' in k]
    zsr_params = [v for k, v in model.named_parameters() if 'zsr_' in k]

    odr_optimizer = torch.optim.SGD(filter(lambda p: p.requires_grad, odr_params),
                                    args.lr1, momentum=args.momentum, weight_decay=args.weight_decay)
    zsr_optimizer = torch.optim.Adam(filter(lambda p: p.requires_grad, zsr_params), args.lr2,
                                     betas=(0.5, 0.999), weight_decay=args.weight_decay)

    optimizer = torch.optim.SGD(filter(lambda p: p.requires_grad, model.parameters()),
                                args.lr1, momentum=args.momentum, weight_decay=args.weight_decay)

    ''' optionally resume from a checkpoint'''
    if args.resume:
        if os.path.isfile(args.resume):
            log_t1 = '=> loading checkpoint {}'.format(args.resume)
            log_print(log_t1, log_dir)
            checkpoint = torch.load(args.resume)
            if (best_prec1 == 0):
                best_prec1 = checkpoint['best_prec1']

            log_t0 = '==========  no attenion only pinyu  =========='
            log_print(log_t0, log_dir)

            log_t2 = '=> pretrained acc {:.4F}'.format(best_prec1)
            log_print(log_t2, log_dir)
            model.load_state_dict(checkpoint['state_dict'])
            log_t3 = '=> loaded checkpoint {} (epoch {})'.format(args.resume, checkpoint['epoch'])
            log_print(log_t3, log_dir)
        else:
            log_t4 = '=> no checkpoint found at {}'.format(args.resume)
            log_print(log_t4, log_dir)

    cudnn.benchmark = True

    traindir = os.path.join('./data', args.data, 'train.list')
    valdir1 = os.path.join('./data', args.data, 'test_seen.list')
    valdir2 = os.path.join('./data', args.data, 'test_unseen.list')

    train_transforms, train_transforms2, val_transforms, val_transforms2 = preprocess_strategy(args.data, args)

    train_dataset = datasets.ImageFolder(img_path, traindir, train_transforms, train_transforms2)

    if args.distributed:
        train_sampler = torch.utils.data.distributed.DistributedSampler(train_dataset)
    else:
        train_sampler = None

    train_loader = torch.utils.data.DataLoader(
        train_dataset, batch_size=args.batch_size, shuffle=(train_sampler is None),
        num_workers=args.workers, pin_memory=True, sampler=train_sampler, drop_last=True)

    val_loader1 = torch.utils.data.DataLoader(
        datasets.ImageFolder(img_path, valdir1, val_transforms, val_transforms2),
        batch_size=args.batch_size, shuffle=False,
        num_workers=args.workers, pin_memory=True, drop_last=True)

    val_loader2 = torch.utils.data.DataLoader(
        datasets.ImageFolder(img_path, valdir2, val_transforms, val_transforms2),
        batch_size=args.batch_size, shuffle=False,
        num_workers=args.workers, pin_memory=True, drop_last=True)

    for epoch in range(args.start_epoch, args.epochs):
        if args.distributed:
            train_sampler.set_epoch(epoch)
        adjust_learning_rate(optimizer, odr_optimizer, zsr_optimizer, epoch, args)

        # train for one epoch
        train(log_dir, train_loader, semantic_data, model, criterion, optimizer, odr_optimizer, zsr_optimizer, epoch,
              is_fix=args.is_fix)

        # evaluate on validation set
        prec1 = validate(val_loader1, val_loader2, semantic_data, model, criterion, log_dir)

        # remember best prec@1 and save checkpoint
        is_best = prec1 > best_prec1
        best_prec1 = max(prec1, best_prec1)

        # save model
        if args.is_fix:
            save_path = os.path.join(args.save_path, 'fix.model')
        else:
            save_path = os.path.join(args.save_path, args.arch + ('_{:.4f}.model').format(best_prec1))
        if is_best:
            save_checkpoint({
                'epoch': epoch + 1,
                'arch': args.arch,
                'state_dict': model.state_dict(),
                'best_prec1': best_prec1,
            }, filename=save_path)
            print('saving!!!!')
        log_text = 'Best_prec:{:.4f};'.format(best_prec1)
        log_print(log_text, log_dir)


def train(log_dir, train_loader, semantic_data, model, criterion, optimizer, odr_optimizer, zsr_optimizer, epoch,
          is_fix):
    # switch to train mode
    model.train()
    if (is_fix):
        freeze_bn(model)

    end = time.time()
    for i, (input, target) in enumerate(train_loader):
        # measure data loading time
        sf = semantic_data['all_att']
        att = sf[target]
        att = torch.tensor(att)
        if args.gpu is not None:
            input = input.cuda(args.gpu, non_blocking=True)
        target = target.cuda(args.gpu, non_blocking=True)
        att = att.cuda(args.gpu, non_blocking=True)
        # compute output
        logits, feats = model(input)
        total_loss, L_odr, L_zsr, L_aux, L_fft = criterion(target, logits, att)

        # compute gradient and do SGD step
        if args.pretrained:
            odr_optimizer.zero_grad()
            L_odr.backward()
            odr_optimizer.step()

            zsr_optimizer.zero_grad()
            (L_zsr + L_aux + L_fft * args.lossw).backward()
            zsr_optimizer.step()
        else:
            optimizer.zero_grad()
            total_loss.backward()
            optimizer.step()

        if i % args.print_freq == 0:
            log_text = 'Epoch: [{}][{}/{}] loss: L_odr {:.4f} L_zsl {:.4f} L_aux {:.4f} L_fft {:.4f} ;'.format(
                epoch, i, len(train_loader), L_odr.item(), L_zsr.item(), L_aux.item(), L_fft.item())
            log_print(log_text, log_dir)


def validate(val_loader1, val_loader2, semantic_data, model, criterion, log_dir):
    ''' load semantic data'''
    seen_c = semantic_data['seen_class']
    unseen_c = semantic_data['unseen_class']
    all_c = semantic_data['all_class']

    # switch to evaluate mode
    model.eval()

    if args.flippingtest:  # flipping test
        test_flip = True
    else:
        test_flip = False

    with torch.no_grad():
        end = time.time()
        for i, (input, target) in enumerate(val_loader1):
            if args.gpu is not None:
                input = input.cuda(args.gpu, non_blocking=True)
            target = target.cuda(args.gpu, non_blocking=True)

            if test_flip:
                [N, M, C, H, W] = input.size()
                input = input.view(N * M, C, H, W)  # flipping test

            # inference
            logits, feats = model(input)

            if test_flip:
                odr_logit = F.softmax(logits[0], dim=1).view(N, M, -1).mean(dim=1).cpu().numpy()
                zsl_logit = F.softmax(logits[1], dim=1).view(N, M, -1).mean(dim=1).cpu().numpy()
            else:
                odr_logit = logits[0].cpu().numpy()
                zsl_logit = logits[1].cpu().numpy()
            zsl_logit_s = zsl_logit.copy();
            zsl_logit_s[:, unseen_c] = -1
            zsl_logit_t = zsl_logit.copy();
            zsl_logit_t[:, seen_c] = -1
            # evaluation
            if (i == 0):
                gt_s = target.cpu().numpy()
                odr_pre_s = np.argmax(odr_logit, axis=1)
                if test_flip:
                    odr_prob_s = odr_logit
                else:
                    odr_prob_s = softmax(odr_logit)
                zsl_pre_sA = np.argmax(zsl_logit, axis=1)
                zsl_pre_sS = np.argmax(zsl_logit_s, axis=1)
                if test_flip:
                    zsl_prob_s = zsl_logit_t
                else:
                    zsl_prob_s = softmax(zsl_logit_t)
            else:
                gt_s = np.hstack([gt_s, target.cpu().numpy()])
                odr_pre_s = np.hstack([odr_pre_s, np.argmax(odr_logit, axis=1)])
                if test_flip:
                    odr_prob_s = np.vstack([odr_prob_s, odr_logit])
                else:
                    odr_prob_s = np.vstack([odr_prob_s, softmax(odr_logit)])
                zsl_pre_sA = np.hstack([zsl_pre_sA, np.argmax(zsl_logit, axis=1)])
                zsl_pre_sS = np.hstack([zsl_pre_sS, np.argmax(zsl_logit_s, axis=1)])
                if test_flip:
                    zsl_prob_s = np.vstack([zsl_prob_s, zsl_logit_t])
                else:
                    zsl_prob_s = np.vstack([zsl_prob_s, softmax(zsl_logit_t)])

        for i, (input, target) in enumerate(val_loader2):
            if args.gpu is not None:
                input = input.cuda(args.gpu, non_blocking=True)
            target = target.cuda(args.gpu, non_blocking=True)

            if test_flip:
                [N, M, C, H, W] = input.size()
                input = input.view(N * M, C, H, W)  # flipping test

            # inference
            logits, feats = model(input)

            if test_flip:
                odr_logit = F.softmax(logits[0], dim=1).view(N, M, -1).mean(dim=1).cpu().numpy()
                zsl_logit = F.softmax(logits[1], dim=1).view(N, M, -1).mean(dim=1).cpu().numpy()
            else:
                odr_logit = logits[0].cpu().numpy()
                zsl_logit = logits[1].cpu().numpy()
            zsl_logit_s = zsl_logit.copy();
            zsl_logit_s[:, unseen_c] = -1
            zsl_logit_t = zsl_logit.copy();
            zsl_logit_t[:, seen_c] = -1

            if (i == 0):
                gt_t = target.cpu().numpy()
                odr_pre_t = np.argmax(odr_logit, axis=1)
                if test_flip:
                    odr_prob_t = odr_logit
                else:
                    odr_prob_t = softmax(odr_logit)
                zsl_pre_tA = np.argmax(zsl_logit, axis=1)
                zsl_pre_tT = np.argmax(zsl_logit_t, axis=1)
                if test_flip:
                    zsl_prob_t = zsl_logit_t
                else:
                    zsl_prob_t = softmax(zsl_logit_t)
            else:
                gt_t = np.hstack([gt_t, target.cpu().numpy()])
                odr_pre_t = np.hstack([odr_pre_t, np.argmax(odr_logit, axis=1)])
                if test_flip:
                    odr_prob_t = np.vstack([odr_prob_t, odr_logit])
                else:
                    odr_prob_t = np.vstack([odr_prob_t, softmax(odr_logit)])
                zsl_pre_tA = np.hstack([zsl_pre_tA, np.argmax(zsl_logit, axis=1)])
                zsl_pre_tT = np.hstack([zsl_pre_tT, np.argmax(zsl_logit_t, axis=1)])
                if test_flip:
                    zsl_prob_t = np.vstack([zsl_prob_t, zsl_logit_t])
                else:
                    zsl_prob_t = np.vstack([zsl_prob_t, softmax(zsl_logit_t)])

        odr_prob = np.vstack([odr_prob_s, odr_prob_t])
        zsl_prob = np.vstack([zsl_prob_s, zsl_prob_t])
        gt = np.hstack([gt_s, gt_t])

        SS = compute_class_accuracy_total(gt_s, zsl_pre_sS, seen_c)
        UU = compute_class_accuracy_total(gt_t, zsl_pre_tT, unseen_c)
        ST = compute_class_accuracy_total(gt_s, zsl_pre_sA, seen_c)
        UT = compute_class_accuracy_total(gt_t, zsl_pre_tA, unseen_c)
        H = 2 * ST * UT / (ST + UT)
        CLS = compute_class_accuracy_total(gt_s, odr_pre_s, seen_c)

        H_opt, S_opt, U_opt, Ds_opt, Du_opt, tau = post_process(odr_prob, zsl_prob, gt, gt_s.shape[0], seen_c, unseen_c,
                                                                args.data)

        
        log_text2 = 'SS: {:.4f} UU: {:.4f} ST: {:.4f} UT: {:.4f} H: {:.4f}'.format(SS, UU, ST, UT, H)
        log_print(log_text2, log_dir)
        log_text3 = 'CLS {:.4f} S_opt: {:.4f} U_opt {:.4f} H_opt {:.4f} Ds_opt {:.4f} Du_opt {:.4f} tau {:.4f}'.format(
            CLS, S_opt, U_opt, H_opt, Ds_opt, Du_opt, tau)
        log_print(log_text3, log_dir)

        H = max(H, H_opt)

    return H


if __name__ == '__main__':
    main()
