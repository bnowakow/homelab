#!/bin/bash

if ! which docker > /dev/null; then
    echo not-present,docker;
    exit
fi

image_name="${1:-wordpress}"

if ! echo $image_name | grep '/' > /dev/null; then
        image_namespace="library/";
else
        image_namespace="";
fi

docker_hub_item_count=$(curl -L -s "https://registry.hub.docker.com/v2/repositories/$image_namespace$image_name/tags?page_size=100" | jq '."count"')
docker_hub_pages_count=$(echo $docker_hub_item_count/100+1 | bc)
# TODO below is to avoid 30s zabbix timeout, try to cache hub pages?
if [ $docker_hub_pages_count -gt 8 ]; then
    docker_hub_pages_count=8
fi
for (( page=1; page<=$docker_hub_pages_count; page++ )); do
    # TODO count actual number of segments and not hardcode [0-9]*\.[0-9]*\.[0-9]
    # https://stackoverflow.com/questions/16679369/count-occurrences-of-a-char-in-a-string-using-bash
    number_of_of_version_segments=$(curl -L -s "https://registry.hub.docker.com/v2/repositories/$image_namespace$image_name/tags?page_size=100&page=$page" | jq '."results"[]["name"]' | egrep '^"[0-9]*\.[0-9]*\.[0-9]*"$' | head -1 | awk -F"." '{print NF}')
    if [ "$number_of_of_version_segments" != "" ]; then
        break;
    fi
done

maximum_number_of_digits_in_version_segment=0
# TODO working currently only for 3 version segments

for (( page=1; page<=$docker_hub_pages_count; page++ )); do
    for version_current in $(curl -L -s "https://registry.hub.docker.com/v2/repositories/$image_namespace$image_name/tags?page_size=100&page=$page" | jq '."results"[]["name"]' | egrep '^"[0-9]*\.[0-9]*\.[0-9]*"$' | sed 's/^\"//' | sed 's/\"$//'); do
        for version_current_segment in $(echo $version_current | sed 's/.*-//' | tr '.' ' '); do
            version_segment_length=$(echo $version_current_segment | wc -c)
            ((version_segment_length--))

            if [ $version_segment_length -gt $maximum_number_of_digits_in_version_segment ]; then
                maximum_number_of_digits_in_version_segment=$version_segment_length
            fi
        done  
    done 
done

# we have to do below for all results from dockerhub and local version :/
# below is to address 1.9 vs 1.19 where string comparison needs 001.009 vs 001.019
newest_numerical_version_current=""
newest_version_current=""
for (( i=0; i<$number_of_of_version_segments; i++ )); do
    newest_numerical_version_current="$newest_numerical_version_current"".""$(printf "%0"$maximum_number_of_digits_in_version_segment"d" 0)"
    newest_version_current=$newest_version_current".0"
done

newest_version_current=$(echo $newest_version_current | sed 's/^\.//')
for (( page=1; page<=$docker_hub_pages_count; page++ )); do
    for version_current in $(curl -L -s "https://registry.hub.docker.com/v2/repositories/$image_namespace$image_name/tags?page_size=100&page=$page" | jq '."results"[]["name"]' | egrep '^"[0-9]*\.[0-9]*\.[0-9]*"$' | sed 's/^\"//' | sed 's/\"$//'); do
        numerical_version_current=""
        for i in $(echo $version_current | sed 's/.*-//' | tr '.' ' '); do
            numerical_version_current=$numerical_version_current.$(printf "%0"$maximum_number_of_digits_in_version_segment"d" $i);
        done
    
        if [ $(echo -e "$newest_numerical_version_current\n$numerical_version_current" | sort | tail -1) = $numerical_version_current ]; then
            # version_current is newer than newest_numerical_version_current
            newest_numerical_version_current=$numerical_version_current
            newest_version_current=$version_current
        fi
    done
done

running_containers=$(docker ps --format '{{.Names}}\t{{.Image}}') || exit 1
found_image=false
outdated_containers=()

while IFS=$'\t' read -r container running_image; do
    case "$running_image" in
        "$image_name":*|*/"$image_name":*) ;;
        *) continue ;;
    esac

    found_image=true
    if [[ "$running_image" != "$image_name:$newest_version_current" ]]; then
        outdated_containers+=("$container=$running_image")
    fi
done <<< "$running_containers"

if $found_image && (( ${#outdated_containers[@]} == 0 )); then
    echo true
else
    printf 'false,%s,%s\n' "${outdated_containers[*]}" "$newest_version_current"
fi
