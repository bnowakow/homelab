#!/usr/bin/env python3
"""Read-only replica placement and Longhorn manager/Fleet maintenance assessment."""
import datetime
import json
from pathlib import Path
import subprocess


def summarize(data):
    replicas=data['replicas']['items'];engines=data['engines']['items']
    volumes=[]
    for volume in data['volumes']['items']:
        name=volume['metadata']['name'];spec=volume['spec'];status=volume.get('status',{})
        related=[r for r in replicas if r['spec'].get('volumeName')==name]
        running=[r for r in related if r.get('status',{}).get('currentState')=='running']
        rw={}
        for engine in engines:
            if engine['spec'].get('volumeName')==name and engine.get('status',{}).get('currentState')=='running':
                rw.update(engine.get('status',{}).get('replicaModeMap') or {})
        volumes.append({'volume':name,'namespace':status.get('kubernetesStatus',{}).get('namespace'),
            'pvc':status.get('kubernetesStatus',{}).get('pvcName'),'state':status.get('state'),
            'robustness':status.get('robustness'),'desiredReplicas':spec.get('numberOfReplicas'),
            'attachedNode':status.get('currentNodeID'),'runningReplicaRecords':len(running),
            'runningReplicaNodes':sorted({r['spec'].get('nodeID','') for r in running}),
            'engineRWReplicaRecords':sum(mode=='RW' for mode in rw.values()),
            'replicas':[{'name':r['metadata']['name'],'node':r['spec'].get('nodeID'),
                'state':r.get('status',{}).get('currentState'),'failedAt':r['spec'].get('failedAt'),
                'evictionRequested':r['spec'].get('evictionRequested'),'engineMode':rw.get(r['metadata']['name'])}
                for r in related]})
    ds=data['manager'];selector=ds['spec']['selector'].get('matchLabels',{})
    manager_pods=[p for p in data['pods']['items'] if all(p['metadata'].get('labels',{}).get(k)==v for k,v in selector.items())]
    return {'volumes':volumes,'manager':{'updateStrategy':ds['spec'].get('updateStrategy'),
        'generation':ds['metadata'].get('generation'),'status':ds.get('status'),
        'templateTolerations':ds['spec']['template']['spec'].get('tolerations',[]),
        'pods':[{'name':p['metadata']['name'],'uid':p['metadata']['uid'],'node':p['spec'].get('nodeName'),
                 'revision':p['metadata'].get('labels',{}).get('controller-revision-hash'),
                 'phase':p.get('status',{}).get('phase'),'deleting':p['metadata'].get('deletionTimestamp'),
                 'containers':[{'name':c['name'],'ready':c.get('ready'),'restarts':c.get('restartCount')}
                               for c in p.get('status',{}).get('containerStatuses',[])]} for p in manager_pods]},
        'counts':{'volumes':len(volumes),'attached':sum(v['state']=='attached' for v in volumes),
                  'attachedDegraded':sum(v['state']=='attached' and v['robustness']=='degraded' for v in volumes),
                  'desiredOne':sum(v['desiredReplicas']==1 for v in volumes)}}


def main():
    kube=['kubectl','--kubeconfig=/etc/rancher/k3s/k3s.yaml','--request-timeout=15s']
    queries={
        'nodes':['get','nodes'],
        'longhorn-nodes':['get','nodes.longhorn.io','-n','longhorn-system'],
        'volumes':['get','volumes.longhorn.io','-n','longhorn-system'],
        'replicas':['get','replicas.longhorn.io','-n','longhorn-system'],
        'engines':['get','engines.longhorn.io','-n','longhorn-system'],
        'manager':['get','daemonset','longhorn-manager','-n','longhorn-system'],
        'pods':['get','pods','-n','longhorn-system'],
        'bundles':['get','bundles.fleet.cattle.io','longhorn','uptime-kuma','-n','fleet-local'],
    }
    folder=Path('/var/tmp')/('longhorn-redundancy-assessment-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(mode=0o700);data={}
    for name,query in queries.items():
        p=subprocess.run(kube+query+['-o','json'],capture_output=True,text=True,timeout=25,check=True)
        data[name]=json.loads(p.stdout)
        (folder/(name+'.json')).write_text(json.dumps(data[name],indent=2)+'\n')
    result=summarize(data)
    result['directory']=str(folder)
    result['kubernetesNodes']=[{'name':n['metadata']['name'],'cordoned':n['spec'].get('unschedulable',False),
        'taints':n['spec'].get('taints',[]),'kubeletVersion':n.get('status',{}).get('nodeInfo',{}).get('kubeletVersion')}
        for n in data['nodes']['items']]
    result['longhornNodes']=[{'name':n['metadata']['name'],'allowScheduling':n['spec'].get('allowScheduling'),
        'conditions':n.get('status',{}).get('conditions'),'disks':n['spec'].get('disks')}
        for n in data['longhorn-nodes']['items']]
    result['bundles']=[{'name':b['metadata']['name'],'paused':b['spec'].get('paused',False),
        'ready':b.get('status',{}).get('summary'),'conditions':b.get('status',{}).get('conditions')}
        for b in data['bundles']['items']]
    (folder/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    # Compact output; all per-volume replica records are in private summary.json.
    compact=dict(result)
    compact['volumes']=[str(row.get('namespace'))+'/'+str(row.get('pvc'))+' ['+row['volume']+'] '+str(row['state'])+'/'+str(row['robustness'])+' desired='+str(row['desiredReplicas'])+' running='+str(row['runningReplicaRecords'])+' RW='+str(row['engineRWReplicaRecords'])+' nodes='+','.join(row['runningReplicaNodes']) for row in result['volumes']]
    compact['limitations']='Running/RW records are not filesystem/backup validation. Kubernetes Node list is not etcd membership. No scheduling, strategy, replica or migration change made.'
    print(json.dumps(compact,indent=2))


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        raise SystemExit('Read-only assessment failed; inventory incomplete: '+str(exc))
