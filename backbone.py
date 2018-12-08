#!/usr/bin/python
# -*- encoding: utf-8 -*-


import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision
import torch.utils.model_zoo as model_zoo


resnet50_url = 'https://download.pytorch.org/models/resnet50-19c8e357.pth'


class SFT(nn.Module):
    def __init__(self, sigma=0.1, *args, **kwargs):
        super(SFT, self).__init__(*args, **kwargs)
        self.sigma = sigma

    def forward(self, emb_org):
        emb_org_norm = torch.norm(emb_org, 2, 1, True).clamp(min=1e-12)
        emb_org_norm = torch.div(emb_org, emb_org_norm)
        W = torch.mm(emb_org_norm, emb_org_norm.t())
        W = torch.div(W, self.sigma)
        T = F.softmax(W, 1)
        emb_sft = torch.mm(T, emb_org)
        return emb_sft



class Embeddor(nn.Module):
    def __init__(self, *args, **kwargs):
        super(Embeddor, self).__init__(*args, **kwargs)
        resnet50 = torchvision.models.resnet50()

        self.conv1 = resnet50.conv1
        self.bn1 = resnet50.bn1
        self.relu = resnet50.relu
        self.maxpool = resnet50.maxpool
        self.layer1 = create_layer(64, 64, 3, stride=1)
        self.layer2 = create_layer(256, 128, 4, stride=2)
        self.layer3 = create_layer(512, 256, 6, stride=2)
        self.layer4 = create_layer(1024, 512, 3, stride=1)
        self.sft = SFT(sigma=0.1)

        # load pretrained weights and initialize added weight
        pretrained_state = model_zoo.load_url(resnet50_url)
        state_dict = self.state_dict()
        for k, v in pretrained_state.items():
            if 'fc' in k: continue
            state_dict.update({k: v})
        self.load_state_dict(state_dict)

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)
        emb_org = F.avg_pool2d(x, x.size()[2:]).view(x.size()[:2])
        emb_sft = self.sft(emb_org)

        return emb_org, emb_sft


class Bottleneck(nn.Module):
    def __init__(self, in_chan, mid_chan, stride=1, stride_at_1x1=False, *args, **kwargs):
        super(Bottleneck, self).__init__(*args, **kwargs)
        stride1x1, stride3x3 = (stride, 1) if stride_at_1x1 else (1, stride)

        out_chan = 4 * mid_chan
        self.conv1 = nn.Conv2d(in_chan, mid_chan, kernel_size=1, stride=stride1x1,
                bias=False)
        self.bn1 = nn.BatchNorm2d(mid_chan)
        self.conv2 = nn.Conv2d(mid_chan, mid_chan, kernel_size=3, stride=stride3x3,
                padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(mid_chan)
        self.conv3 = nn.Conv2d(mid_chan, out_chan, kernel_size=1, bias=False)
        self.bn3 = nn.BatchNorm2d(out_chan)
        self.relu = nn.ReLU(inplace=True)

        self.downsample = None
        if in_chan != out_chan or stride != 1:
            self.downsample = nn.Sequential(
                nn.Conv2d(in_chan, out_chan, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm2d(out_chan))

    def forward(self, x):
        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)
        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)
        out = self.conv3(out)
        out = self.bn3(out)

        if self.downsample == None:
            residual = x
        else:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


def create_layer(in_chan, mid_chan, b_num, stride):
    out_chan = mid_chan * 4
    blocks = [Bottleneck(in_chan, mid_chan, stride=stride),]
    for i in range(1, b_num):
        blocks.append(Bottleneck(out_chan, mid_chan, stride=1))
    return nn.Sequential(*blocks)



if __name__ == '__main__':
    intensor = torch.randn(10, 3, 256, 128)
    intensor = intensor.cuda()
    net = Embeddor()
    net.cuda()
    net.train()
    net = nn.DataParallel(net)
    out_org, out_sft = net(intensor)
    print(out_org.shape)
    print(out_sft.shape)
